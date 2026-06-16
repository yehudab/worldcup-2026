"""Persistent bet ledger (SQLite).

The bot records a predicted scoreline for one upcoming game (``place``), then
later asks the service to grade that bet against the actual result (``grade``).
Bets live in a SQLite DB on the CACHE_DIR volume, mirroring the sibling
connections-scorer service's storage approach (sqlite3.Row + WAL + a ``db()``
context manager).

A bet row is one prediction for one game:
    team1/team2  canonical names, in the order they were predicted
    pair_key     sorted "A|B" so a pair is found regardless of argument order
    match_date   date of the game (YYYY-MM-DD) — REQUIRED, so the same two teams
                 meeting twice (group + knockout) are kept as distinct bets and
                 never overwrite each other
    pred1/pred2  predicted goals for team1/team2 (NULL for a winner-only bet)
    pred_winner  predicted winner team name, or "draw"
"""

import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get(
    "BETS_DB_PATH",
    os.path.join(os.environ.get("CACHE_DIR", "/data"), "bets.db"),
)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def db():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                placed_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                team1       TEXT NOT NULL,
                team2       TEXT NOT NULL,
                pair_key    TEXT NOT NULL,
                match_date  TEXT NOT NULL,
                pred1       INTEGER,
                pred2       INTEGER,
                pred_winner TEXT,
                UNIQUE(pair_key, match_date)
            );
            CREATE INDEX IF NOT EXISTS idx_bets_pair ON bets(pair_key);
            CREATE INDEX IF NOT EXISTS idx_bets_date ON bets(match_date);
        """)


def _pair_key(a, b):
    return "|".join(sorted([a, b]))


def _row_to_bet(row):
    if row is None:
        return None
    pred_ft = None
    if row["pred1"] is not None and row["pred2"] is not None:
        pred_ft = [row["pred1"], row["pred2"]]
    return {
        "id": row["id"],
        "placed_at": row["placed_at"],
        "team1": row["team1"],
        "team2": row["team2"],
        "match_date": row["match_date"],
        "pred_ft": pred_ft,
        "pred_winner": row["pred_winner"],
    }


def place(team1, team2, pred_ft, pred_winner, match_date):
    """Save the bet for one game, replacing any prior bet on the same
    (team-pair, match_date). ``match_date`` is required. Returns the record."""
    pair = _pair_key(team1, team2)
    p1, p2 = (pred_ft[0], pred_ft[1]) if pred_ft else (None, None)
    with db() as conn:
        # Re-betting the same game overwrites the previous prediction. Keyed on
        # (pair, date) so a later knockout meeting of the same teams is separate.
        conn.execute(
            "DELETE FROM bets WHERE pair_key = ? AND match_date = ?", (pair, match_date)
        )
        cur = conn.execute(
            "INSERT INTO bets (team1, team2, pair_key, match_date, pred1, pred2, pred_winner) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (team1, team2, pair, match_date, p1, p2, pred_winner),
        )
        row = conn.execute("SELECT * FROM bets WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_bet(row)


def for_teams(a, b):
    """The most recently placed bet on this pair of teams, or None."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM bets WHERE pair_key = ? ORDER BY placed_at DESC, id DESC LIMIT 1",
            (_pair_key(a, b),),
        ).fetchone()
        return _row_to_bet(row)


def for_date(date):
    """All bets whose game is on ``date`` (YYYY-MM-DD), oldest first."""
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM bets WHERE match_date = ? ORDER BY placed_at, id", (date,)
        ).fetchall()
        return [_row_to_bet(r) for r in rows]


def grade(bet, match):
    """Compare a bet against the actual finished match (pure).

    ``match`` is a normalized match (football.normalize_match) that is finished,
    or None / not-yet-finished. Returns a verdict dict.
    """
    if not match or match.get("status") != "finished":
        return {"status": "pending",
                "message": "the game hasn't concluded yet (or no result found)"}

    actual_ft = list(match["score"]["ft"])
    actual_winner = match["winner"]

    # Align the predicted score to the actual match's team order.
    pred_ft = bet.get("pred_ft")
    aligned_pred = None
    if pred_ft is not None:
        aligned_pred = (list(pred_ft) if bet["team1"] == match["team1"]
                        else [pred_ft[1], pred_ft[0]])

    exact = aligned_pred is not None and aligned_pred == actual_ft
    outcome_correct = bet.get("pred_winner") == actual_winner
    result = "exact" if exact else ("outcome" if outcome_correct else "miss")
    return {
        "status": "graded",
        "result": result,                 # exact | outcome | miss
        "exact_score": exact,
        "outcome_correct": outcome_correct,
        "predicted": {"team1": match["team1"], "team2": match["team2"],
                      "ft": aligned_pred, "winner": bet.get("pred_winner")},
        "actual": {"team1": match["team1"], "team2": match["team2"],
                   "ft": actual_ft, "winner": actual_winner},
        "match": match,
    }
