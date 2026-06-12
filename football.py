"""Domain logic: time normalization, standings, fixtures, head-to-head, form.

All functions are pure over data passed in — fetching/caching lives in
data_source.py. This keeps the logic easy to test and independent of where the
data comes from.
"""

import re
from datetime import datetime, timedelta, timezone

_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?:\s*UTC\s*([+-]\d{1,2})(?::?(\d{2}))?)?")


def parse_kickoff(date_str, time_str):
    """Parse a date + openfootball time ("13:00 UTC-6") into local + UTC.

    Returns a dict with the original ``local`` string, an ISO ``utc`` timestamp
    (when an offset is present), and the numeric ``offset`` in hours.
    """
    out = {"local": time_str or None, "utc": None, "offset": None}
    if not time_str:
        return out
    m = _TIME_RE.match(time_str)
    if not m:
        return out
    hh, mm = int(m.group(1)), int(m.group(2))
    if m.group(3) is None:
        return out  # no offset given; can't normalize to UTC
    offset_h = int(m.group(3))
    offset_m = int(m.group(4)) if m.group(4) else 0
    out["offset"] = offset_h
    try:
        y, mo, d = (int(x) for x in date_str.split("-"))
    except (ValueError, AttributeError):
        return out
    tz = timezone(timedelta(hours=offset_h, minutes=offset_m if offset_h >= 0 else -offset_m))
    local_dt = datetime(y, mo, d, hh, mm, tzinfo=tz)
    out["utc"] = local_dt.astimezone(timezone.utc).isoformat()
    return out


def _winner(team1, team2, score):
    """Return the winning team name, 'draw', or None (not finished)."""
    if not score:
        return None
    # Penalties decide first, then full-time.
    if score.get("p"):
        a, b = score["p"]
        return team1 if a > b else team2
    ft = score.get("ft")
    if not ft:
        return None
    a, b = ft
    if a > b:
        return team1
    if b > a:
        return team2
    return "draw"


def normalize_match(m, year=None):
    """Turn a raw openfootball match into a clean, self-describing dict."""
    score = m.get("score")
    finished = bool(score and score.get("ft"))
    kickoff = parse_kickoff(m.get("date"), m.get("time"))
    return {
        "year": year,
        "round": m.get("round"),
        "group": m.get("group"),
        "date": m.get("date"),
        "time_local": kickoff["local"],
        "time_utc": kickoff["utc"],
        "utc_offset": kickoff["offset"],
        "team1": m.get("team1"),
        "team2": m.get("team2"),
        "ground": m.get("ground"),
        "status": "finished" if finished else "scheduled",
        "score": score if finished else None,
        "winner": _winner(m.get("team1"), m.get("team2"), score) if finished else None,
    }


def normalize_all(matches_doc, year=None):
    return [normalize_match(m, year) for m in matches_doc.get("matches", [])]


# --- standings ------------------------------------------------------------
def _blank_row(team):
    return {"team": team, "played": 0, "won": 0, "drawn": 0, "lost": 0,
            "gf": 0, "ga": 0, "gd": 0, "points": 0}


def standings(matches, groups_doc, group=None):
    """Compute group tables from finished group-stage matches.

    ``matches`` are normalized matches for the tournament. ``groups_doc`` is the
    raw groups file (gives the full team list so 0-game teams still appear).
    Returns a dict ``{group_name: [rows sorted with rank]}``.
    """
    wanted = None
    if group:
        g = group.strip().upper()
        wanted = g if g.startswith("GROUP") else f"GROUP {g}"

    tables = {}
    for grp in groups_doc.get("groups", []):
        name = grp["name"]
        if wanted and name.upper() != wanted:
            continue
        tables[name] = {t: _blank_row(t) for t in grp["teams"]}

    for m in matches:
        gname = m.get("group")
        if not gname or gname not in tables or m["status"] != "finished":
            continue
        ft = m["score"]["ft"]
        t1, t2 = m["team1"], m["team2"]
        rows = tables[gname]
        if t1 not in rows or t2 not in rows:
            continue
        g1, g2 = ft[0], ft[1]
        for team, gf, ga in ((t1, g1, g2), (t2, g2, g1)):
            r = rows[team]
            r["played"] += 1
            r["gf"] += gf
            r["ga"] += ga
            r["gd"] = r["gf"] - r["ga"]
        if g1 > g2:
            rows[t1]["won"] += 1; rows[t1]["points"] += 3; rows[t2]["lost"] += 1
        elif g2 > g1:
            rows[t2]["won"] += 1; rows[t2]["points"] += 3; rows[t1]["lost"] += 1
        else:
            rows[t1]["drawn"] += 1; rows[t1]["points"] += 1
            rows[t2]["drawn"] += 1; rows[t2]["points"] += 1

    result = {}
    for name, rows in tables.items():
        ordered = sorted(rows.values(),
                         key=lambda r: (-r["points"], -r["gd"], -r["gf"], r["team"]))
        for i, r in enumerate(ordered, start=1):
            r["rank"] = i
        result[name] = ordered
    return result


# --- fixtures / results ---------------------------------------------------
def matches_on(matches, date):
    return [m for m in matches if m["date"] == date]


def _involves(m, team):
    return m["team1"] == team or m["team2"] == team


def team_fixtures(matches, team):
    """All of a team's matches, sorted by date (then kickoff)."""
    fx = [m for m in matches if _involves(m, team)]
    return sorted(fx, key=lambda m: (m["date"] or "", m["time_utc"] or ""))


def team_next(matches, team):
    upcoming = [m for m in team_fixtures(matches, team) if m["status"] == "scheduled"]
    return upcoming[0] if upcoming else None


def team_last(matches, team):
    played = [m for m in team_fixtures(matches, team) if m["status"] == "finished"]
    return played[-1] if played else None


# --- cross-year context (head-to-head, form) ------------------------------
def head_to_head(all_matches, a, b):
    """Past meetings between two teams across all tournaments (finished only)."""
    pair = {a, b}
    meetings = [m for m in all_matches
                if m["status"] == "finished" and {m["team1"], m["team2"]} == pair]
    meetings.sort(key=lambda m: (m["year"] or 0, m["date"] or ""))
    summary = {a: 0, b: 0, "draws": 0}
    for m in meetings:
        if m["winner"] == "draw":
            summary["draws"] += 1
        elif m["winner"] in summary:
            summary[m["winner"]] += 1
    return {"played": len(meetings), "summary": summary, "meetings": meetings}


def recent_form(all_matches, team, n=5):
    """A team's last ``n`` finished World Cup matches, most recent first."""
    played = [m for m in all_matches if m["status"] == "finished" and _involves(m, team)]
    played.sort(key=lambda m: (m["year"] or 0, m["date"] or ""), reverse=True)
    recent = played[:n]
    record = {"won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0}
    for m in recent:
        ft = m["score"]["ft"]
        gf, ga = (ft[0], ft[1]) if m["team1"] == team else (ft[1], ft[0])
        record["gf"] += gf
        record["ga"] += ga
        if m["winner"] == "draw":
            record["drawn"] += 1
        elif m["winner"] == team:
            record["won"] += 1
        else:
            record["lost"] += 1
    return {"record": record, "matches": recent}


def fixture_context(current_matches, all_matches, groups_doc, a, b):
    """Everything the bot's LLM needs to predict a scoreline for a vs b."""
    scheduled = None
    pair = {a, b}
    for m in team_fixtures(current_matches, a):
        if {m["team1"], m["team2"]} == pair:
            scheduled = m
            break
    table = standings(current_matches, groups_doc)
    return {
        "teams": [a, b],
        "scheduled": scheduled,  # null if these two aren't paired in the schedule yet
        "head_to_head": head_to_head(all_matches, a, b),
        "form": {a: recent_form(all_matches, a), b: recent_form(all_matches, b)},
        "group_standings": {
            a: _standing_of(table, a),
            b: _standing_of(table, b),
        },
    }


def _standing_of(table, team):
    for name, rows in table.items():
        for r in rows:
            if r["team"] == team:
                return {"group": name, **r}
    return None
