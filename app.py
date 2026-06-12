"""HTTP API exposing World Cup data to the PicoClaw bot.

Runs as a small service on the bot's Docker network (default http://worldcup:5001).
Every endpoint returns JSON. The bot reaches it via workspace/worldcup.sh.
"""

import os
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request

import football
from data_source import ALL_YEARS, CURRENT_YEAR, source

app = Flask(__name__)

DEFAULT_YEAR = CURRENT_YEAR


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
