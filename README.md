# worldcup-2026

A small HTTP service that answers FIFA World Cup 2026 questions for the PicoClaw
WhatsApp bot — who's playing today, group standings, a team's next game,
yesterday's results, and a context bundle for predicting upcoming scorelines.

It runs as a sibling container on the bot's Docker network (default
`http://worldcup:5001`); the bot reaches it through `workspace/worldcup.sh`.

## Data source

The MVP reads the semi-offline [openfootball/worldcup.json](https://github.com/openfootball/worldcup.json)
repo over HTTP and caches files on disk (short TTL for the live 2026 tournament,
long TTL for immutable past tournaments). Standings are **computed** from match
results — the source provides only group/team lists.

**Swapping in a paid live API later:** everything talks to the small interface
in `data_source.py` (`matches` / `groups` / `stadiums`). Write a new class with
those three methods and replace the `source = OpenFootballSource()` line — no
other code changes.

## Run locally

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
CACHE_DIR=./cache python app.py        # binds :5001
```

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness check |
| `GET /today?date=YYYY-MM-DD` | Matches on a date (default: today, UTC) |
| `GET /results?date=yesterday\|YYYY-MM-DD` | Finished matches + scores for a date |
| `GET /standings?group=A` | Computed group table(s) |
| `GET /next?team=Mexico` | Team's next scheduled match |
| `GET /last?team=Mexico` | Team's last result |
| `GET /schedule?team=Mexico` | All of a team's fixtures |
| `GET /fixture?team1=Qatar&team2=Switzerland` | Prediction context: H2H + form + standings |

All endpoints accept `?year=` (default 2026) and return JSON. Team names accept
Hebrew/variants (resolved in `teams.py`); `date` accepts `today`/`yesterday`/`tomorrow`.

## Layout

- `app.py` — Flask endpoints (thin; delegates to `football.py`).
- `football.py` — domain logic: standings, fixtures, H2H, form, time normalization.
- `data_source.py` — swappable fetch + on-disk cache layer.
- `teams.py` — team-name normalization (Hebrew/variant → canonical).
