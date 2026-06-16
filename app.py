"""HTTP API exposing World Cup data to the PicoClaw bot.

Runs as a small service on the bot's Docker network (default http://worldcup:5001).
Every endpoint returns JSON. The bot reaches it via workspace/worldcup.sh.
"""

import os
import re
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request

import bets as bets_mod
import football
from data_source import ALL_YEARS, CURRENT_YEAR, source

app = Flask(__name__)

DEFAULT_YEAR = CURRENT_YEAR

bets_mod.init_db()


# --- helpers --------------------------------------------------------------
def _year():
    try:
        return int(request.args.get("year", DEFAULT_YEAR))
    except (TypeError, ValueError):
        return DEFAULT_YEAR


def _current_matches(year):
    return football.normalize_all(source.matches(year), year)


def _known_teams(year):
    teams = set()
    for grp in source.groups(year).get("groups", []):
        teams.update(grp.get("teams", []))
    return teams


def _resolve(year, raw_name):
    """Resolve a (possibly Hebrew/variant) team name to canonical, or None."""
    import teams as teams_mod
    return teams_mod.resolve(raw_name, _known_teams(year))


def _all_matches():
    """Normalized matches across every tournament (for H2H / form)."""
    out = []
    for y in ALL_YEARS:
        try:
            out.extend(football.normalize_all(source.matches(y), y))
        except Exception:
            continue  # a missing/unreachable year shouldn't break the request
    return out


def _resolve_date(value):
    """Map 'today'/'yesterday'/'tomorrow' or an explicit YYYY-MM-DD to a date string."""
    if not value:
        return datetime.now(timezone.utc).date().isoformat()
    v = value.strip().lower()
    today = datetime.now(timezone.utc).date()
    if v == "today":
        return today.isoformat()
    if v == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    if v == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    return value.strip()


def _team_not_found(raw):
    return jsonify({
        "error": "team_not_found",
        "message": f"Could not match a team to '{raw}'. Use the English name (e.g. Mexico, Switzerland).",
    }), 404


def _parse_pred(pred, pred1, pred2):
    """Return a [goals1, goals2] predicted score from either a 'a-b'/'a:b'
    string or two separate numbers, or None if no usable score was given."""
    if pred:
        parts = re.split(r"[-:]", pred.strip())
        if len(parts) == 2:
            try:
                return [int(parts[0]), int(parts[1])]
            except ValueError:
                return None
        return None
    if pred1 is not None and pred2 is not None:
        try:
            return [int(pred1), int(pred2)]
        except (TypeError, ValueError):
            return None
    return None


def _match_for_bet(matches, bet):
    """The actual match a bet refers to: the meeting of its two teams on the
    bet's date if present, else the most recent finished meeting."""
    pair = {bet["team1"], bet["team2"]}
    same = [m for m in matches if {m["team1"], m["team2"]} == pair]
    for m in same:
        if m["date"] == bet["match_date"]:
            return m
    finished = sorted((m for m in same if m["status"] == "finished"),
                      key=lambda m: m["date"] or "")
    return finished[-1] if finished else None


# --- endpoints ------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify({"status": "ok", "year": DEFAULT_YEAR})


@app.get("/today")
@app.get("/matches")
def matches_today():
    year = _year()
    date = _resolve_date(request.args.get("date"))
    return jsonify({"date": date, "year": year,
                    "matches": football.matches_on(_current_matches(year), date)})


@app.get("/results")
def results():
    year = _year()
    date = _resolve_date(request.args.get("date") or "yesterday")
    finished = [m for m in football.matches_on(_current_matches(year), date)
                if m["status"] == "finished"]
    return jsonify({"date": date, "year": year, "matches": finished})


@app.get("/standings")
def standings():
    year = _year()
    group = request.args.get("group")
    table = football.standings(_current_matches(year), source.groups(year), group)
    return jsonify({"year": year, "standings": table})


@app.get("/next")
def team_next():
    year = _year()
    raw = request.args.get("team")
    team = _resolve(year, raw)
    if not team:
        return _team_not_found(raw)
    return jsonify({"team": team, "match": football.team_next(_current_matches(year), team)})


@app.get("/last")
def team_last():
    year = _year()
    raw = request.args.get("team")
    team = _resolve(year, raw)
    if not team:
        return _team_not_found(raw)
    return jsonify({"team": team, "match": football.team_last(_current_matches(year), team)})


@app.get("/schedule")
def team_schedule():
    year = _year()
    raw = request.args.get("team")
    team = _resolve(year, raw)
    if not team:
        return _team_not_found(raw)
    return jsonify({"team": team, "matches": football.team_fixtures(_current_matches(year), team)})


@app.get("/fixture")
@app.get("/predict")  # "predict" is an accepted synonym for "fixture"
def fixture():
    year = _year()
    raw1, raw2 = request.args.get("team1"), request.args.get("team2")
    t1, t2 = _resolve(year, raw1), _resolve(year, raw2)
    if not t1:
        return _team_not_found(raw1)
    if not t2:
        return _team_not_found(raw2)
    ctx = football.fixture_context(_current_matches(year), _all_matches(),
                                   source.groups(year), t1, t2)
    return jsonify(ctx)


@app.get("/bets/place")
def bets_place():
    """Record the bot's predicted score for one upcoming game."""
    year = _year()
    raw1, raw2 = request.args.get("team1"), request.args.get("team2")
    t1, t2 = _resolve(year, raw1), _resolve(year, raw2)
    if not t1:
        return _team_not_found(raw1)
    if not t2:
        return _team_not_found(raw2)

    pred_ft = _parse_pred(request.args.get("pred"),
                          request.args.get("pred1"), request.args.get("pred2"))
    if pred_ft is not None:
        a, b = pred_ft
        pred_winner = t1 if a > b else t2 if b > a else "draw"
    else:
        w = (request.args.get("winner") or "").strip()
        if not w:
            return jsonify({"error": "bad_bet",
                            "message": "provide a predicted score (pred=2-1) or winner=."}), 400
        pred_winner = "draw" if w.lower() in ("draw", "tie", "x") else (_resolve(year, w) or w)

    # A bet must be tied to a dated game so it can never collide with another
    # meeting of the same teams (e.g. a later knockout). Prefer the scheduled
    # game's date; otherwise the caller must pass one explicitly.
    current = _current_matches(year)
    pair = {t1, t2}
    scheduled = next((m for m in football.team_fixtures(current, t1)
                      if {m["team1"], m["team2"]} == pair and m["status"] == "scheduled"), None)
    match_date = request.args.get("date") or (scheduled["date"] if scheduled else None)
    if not match_date:
        return jsonify({
            "error": "date_required",
            "message": (f"No scheduled game found for {t1} vs {t2} (e.g. a future knockout). "
                        "Pass date=YYYY-MM-DD for the game you're betting on."),
        }), 400

    record = bets_mod.place(t1, t2, pred_ft, pred_winner, match_date)
    return jsonify({"status": "placed", "bet": record, "scheduled": scheduled})


@app.get("/bets/review")
def bets_review():
    """Grade saved bets against actual results.

    Two modes: team1+team2 (the latest bet on that pairing), or date=
    (every bet whose game is on that date)."""
    year = _year()
    current = _current_matches(year)
    raw1, raw2 = request.args.get("team1"), request.args.get("team2")
    date = request.args.get("date")

    if raw1 and raw2:
        t1, t2 = _resolve(year, raw1), _resolve(year, raw2)
        if not t1:
            return _team_not_found(raw1)
        if not t2:
            return _team_not_found(raw2)
        bet = bets_mod.for_teams(t1, t2)
        if not bet:
            return jsonify({"status": "no_bet",
                            "message": f"No saved bet for {t1} vs {t2}."}), 404
        return jsonify({"mode": "teams",
                        "review": {"bet": bet, **bets_mod.grade(bet, _match_for_bet(current, bet))}})

    if date:
        d = _resolve_date(date)
        reviews = [{"bet": bet, **bets_mod.grade(bet, _match_for_bet(current, bet))}
                   for bet in bets_mod.for_date(d)]
        summary = {
            "date": d,
            "total": len(reviews),
            "exact": sum(1 for r in reviews if r.get("result") == "exact"),
            "outcome": sum(1 for r in reviews if r.get("result") == "outcome"),
            "miss": sum(1 for r in reviews if r.get("result") == "miss"),
            "pending": sum(1 for r in reviews if r.get("status") == "pending"),
        }
        return jsonify({"mode": "date", "summary": summary, "reviews": reviews})

    return jsonify({"error": "bad_request",
                    "message": "review needs team1+team2, or date=YYYY-MM-DD."}), 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
