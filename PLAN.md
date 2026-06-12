# Plan: Turn PicoClaw into a FIFA World Cup 2026 expert

## Context

PicoClaw is a Go-based WhatsApp assistant ("פיקו מנשה") that currently scores NYT Connections
screenshots. The bot itself runs as a tiny Go binary on Alpine and **cannot run Python** — it
reaches helper services over the `botnet` Docker network via shell scripts in its workspace
(e.g. `score.sh` curls `http://scorer:5000`).

We want the bot to also answer World Cup 2026 questions:
- Who is playing today?
- Who leads Group A?
- When/where is Mexico's next game?
- What were yesterday's results?
- Predicted scoreline for an upcoming match (e.g. Qatar vs Switzerland).

Two confirmed decisions:
- **Data delivery**: a small Flask **HTTP microservice** on `botnet` (mirrors the `scorer` service).
- **Prediction**: Python returns rich **context** (head-to-head history, recent form, standings);
  the **bot's LLM reasons over it** to produce the predicted scoreline + justification.

Data source for MVP: the semi-offline `openfootball/worldcup.json` repo
(`https://raw.githubusercontent.com/openfootball/worldcup.json/master/<year>/<file>`), behind a
swappable interface so a paid live API can replace it later.

### Data shape (verified)
- `2026/worldcup.json` → `{ "name", "matches": [...] }`. Each match:
  `round`, `date` (`YYYY-MM-DD`), `time` (`"13:00 UTC-6"` — stadium-local + offset),
  `team1`, `team2`, `group` (group stage only), `ground`, optional `score` (`{ft,ht,et,p}` arrays),
  `goals1`/`goals2` (each `{name, minute(str), penalty?, owngoal?, offset?}`).
- `2026/worldcup.groups.json` → `{ "name", "groups": [ {name, teams[4]} ] }` (12 groups A–L).
  **Standings are NOT provided — they must be computed from results.** Verified groups:
  A: Mexico, South Africa, South Korea, Czech Republic · B: Canada, Bosnia & Herzegovina, Qatar,
  Switzerland · C: Brazil, Morocco, Haiti, Scotland · D: USA, Paraguay, Australia, Turkey ·
  E: Germany, Curaçao, Ivory Coast, Ecuador · F: Netherlands, Japan, Sweden, Tunisia ·
  G: Belgium, Egypt, Iran, New Zealand · H: Spain, Cape Verde, Saudi Arabia, Uruguay ·
  I: France, Senegal, Iraq, Norway · J: Argentina, Algeria, Austria, Jordan ·
  K: Portugal, DR Congo, Uzbekistan, Colombia · L: England, Croatia, Ghana, Panama.
- Also available per year: `worldcup.stadiums.json`, `worldcup.teams.json`, `worldcup.squads.json`.
- Historical tournaments 1930→2026 use the same schema (enables H2H/form across years).

---

## Part 1 — Python data service (`/Users/yehuda/Dev/worldcup-2026`, currently empty except LICENSE/.git)

### Files
```
worldcup-2026/
├── app.py            # Flask app + HTTP endpoints (thin; delegates to football.py)
├── data_source.py    # Swappable data layer: OpenFootballSource (fetch + on-disk cache w/ TTL)
├── football.py       # Domain logic: standings, find matches, H2H, form, time normalization
├── teams.py          # Team-name normalization + alias map (Hebrew/variant → openfootball English)
├── requirements.txt  # flask, requests
├── Dockerfile        # python:3.12-slim, pip install, expose 5001, run app.py
├── .gitignore        # __pycache__, cache dir, .env
└── README.md         # run + endpoint docs, "swap to paid API" note
```

### `data_source.py` — swappable layer (key to the "switch to paid API later" requirement)
- Class `OpenFootballSource` with methods `matches(year)`, `groups(year)`, `stadiums(year)`.
- Fetches raw GitHub URLs with `requests`; caches JSON on disk (`/data`, mounted volume) with a
  TTL — **short TTL (~10 min) for the current year 2026** (live results change), **long/permanent
  for past years** (immutable). Falls back to stale cache on network failure (semi-offline).
- Define a minimal informal interface (the methods above) so a future `PaidApiSource` is a drop-in.

### `football.py` — domain logic
- `normalize_match(m)`: parse `time` (`"13:00 UTC-6"`) into both `local_time` and a derived
  `utc` ISO timestamp; classify status: `finished` (has `score.ft`), else `scheduled`.
- `standings(year, group)`: compute table from finished group-stage matches — played, W/D/L, GF,
  GA, GD, points (3/1/0), sorted by points→GD→GF, with `rank`. Return all groups or one.
- `matches_on(year, date)`: matches for a date (used by today/results).
- `team_fixtures(year, team)`, `team_next(team)`, `team_last(team)`.
- `head_to_head(teamA, teamB)`: scan **all years** for past meetings → list + summary (W/W/D counts).
- `recent_form(team, n)`: last N finished results (current + prior tournaments) → W/D/L + goals.
- `fixture_context(teamA, teamB)`: bundle scheduled fixture (if any) + H2H + both teams' form +
  both teams' current group standing → the prediction payload. If no scheduled match exists yet
  (e.g. a hypothetical knockout pairing not in the schedule), still return H2H + form and flag
  `scheduled: null`. (Qatar vs Switzerland IS a real Group B fixture — see groups below.)

### `app.py` — endpoints (all default `year=2026`, return JSON)
- `GET /health`
- `GET /matches?date=YYYY-MM-DD` and `GET /today?date=...` — date defaults to server UTC date;
  **bot passes an explicit Israel-derived date** (see skill). Returns matches w/ status + score.
- `GET /results?date=yesterday|YYYY-MM-DD` — finished matches w/ scores for that date.
- `GET /standings` and `GET /standings?group=A` — computed table(s).
- `GET /team/<name>/next`, `GET /team/<name>/last`, `GET /team/<name>/schedule`.
- `GET /fixture?team1=Qatar&team2=Switzerland` — prediction context bundle (`fixture_context`).
- Team names resolved via `teams.normalize()` (case-insensitive + alias map) so Hebrew/variants work.

### `Dockerfile`
`FROM python:3.12-slim`; copy, `pip install -r requirements.txt`; `EXPOSE 5001`;
`CMD ["python","app.py"]` binding `0.0.0.0:5001`. Cache dir `/data`.

---

## Part 2 — Bot integration (`/Users/yehuda/Dev/picoclaw/docker/data/workspace/`)

### New workspace dispatcher script — `worldcup.sh`
Mirror the style of `stats.sh`. `BASE="${WORLDCUP_URL:-http://worldcup:5001}"`. Subcommands:
```
worldcup.sh today [date]          worldcup.sh next <team>
worldcup.sh results <date>        worldcup.sh last <team>
worldcup.sh standings [group]     worldcup.sh schedule <team>
worldcup.sh fixture <team1> <team2>
```
Each `curl -s` the matching endpoint (URL-encode team names). Mark executable.

### New capability doc — `WORLDCUP.md` (the "skill" MD file)
The bot's playbook for World Cup questions. Contents:
- **Triggers**: messages about World Cup / מונדיאל / games / groups / scores / specific national teams.
- **Always use full path** `/home/picoclaw/.picoclaw/workspace/worldcup.sh` (matches existing convention).
- **Timezone rule**: get Israel time via `time.sh` first; pass the Israel date to `today`/`results`;
  convert each match's returned `utc` field to Israel time when stating kickoff times.
- **Team names**: translate Hebrew/colloquial names to English before calling (e.g. מקסיקו→Mexico);
  the service also fuzzy-matches as a safety net.
- **Per-question recipes** mapping each example question to a subcommand and an answer format
  (concise, Hebrew default, end with 🤖 per SOUL.md).
- **Prediction recipe**: call `fixture <t1> <t2>`, then reason over H2H + form + standings to give a
  predicted scoreline **with a one-line justification**, clearly framed as a prediction/opinion
  (not a fact). Handle the "no scheduled fixture" case gracefully.

### Update `SOUL.md`
- Add a **Group Behavior** trigger line: if the message is about the World Cup / מונדיאל / a national
  team / fixtures / standings, follow `WORLDCUP.md`.
- Add a short **World Cup 2026** section that points to `WORLDCUP.md` for the full playbook (keep the
  heavy detail in WORLDCUP.md to keep SOUL.md scannable).

### Update `IDENTITY.md`
- Expand **Purpose** to add the second role: FIFA World Cup 2026 expert that answers questions about
  fixtures, results, group standings, and offers match predictions. Keep Connections as role #1.

### Update `docker/docker-compose.yml`
Add a `worldcup` service (gateway profile, on `botnet`), mirroring `scorer`:
```yaml
  worldcup:
    build:
      context: ../../worldcup-2026      # resolves to /Users/yehuda/Dev/worldcup-2026
      dockerfile: Dockerfile
    container_name: worldcup-data
    restart: on-failure
    profiles: [gateway]
    volumes:
      - worldcup-cache:/data
    networks: [botnet]
```
Add `worldcup-cache:` under top-level `volumes:`. (On the production server the repo must be checked
out at the sibling path `…/worldcup-2026`, same as `connections-score-analyzer` for `scorer`.)

---

## Verification

**Python service (local, no Docker):**
```
cd /Users/yehuda/Dev/worldcup-2026
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python app.py            # binds :5001
# in another shell — exercise the example questions:
curl -s "localhost:5001/today?date=2026-06-12"          # who's playing today
curl -s "localhost:5001/standings?group=A"              # Group A leader (Mexico expected on top)
curl -s "localhost:5001/team/Mexico/next"               # next Mexico game + venue
curl -s "localhost:5001/results?date=yesterday"         # yesterday's results
curl -s "localhost:5001/fixture?team1=Qatar&team2=Switzerland"  # prediction bundle (real Group B fixture)
```
Confirm: standings math (3/1/0, GD tiebreak), `utc` time normalization, finished-vs-scheduled status,
H2H pulls from prior years, graceful handling when a fixture pairing isn't in the schedule (e.g. a
hypothetical knockout matchup). Qatar vs Switzerland returns a real Group B fixture.

**Docker (mirrors production):**
```
docker compose -f /Users/yehuda/Dev/picoclaw/docker/docker-compose.yml --profile gateway build worldcup
docker compose -f /Users/yehuda/Dev/picoclaw/docker/docker-compose.yml --profile gateway up -d worldcup
docker run --rm --network docker_botnet curlimages/curl -s http://worldcup:5001/standings?group=A
```

**Bot end-to-end (WhatsApp):** ask "מי משחק היום?", "מי מובילה בבית A?", "מתי המשחק הבא של מקסיקו?",
"מה היו התוצאות אתמול?", and a prediction question — verify the bot calls `worldcup.sh`, converts
times to Israel time, answers in Hebrew, and ends with 🤖.

---

## Deployment (manual — dev machine ≠ runtime)

This machine is **dev only**. The bot runs 24/7 on a **small VPS** via Docker Compose. There is no
auto-deploy — going live is manual:
1. Commit + push **both** repos to GitHub: `worldcup-2026` (new service) and `picoclaw`
   (workspace files + `docker-compose.yml`).
2. On the VPS, `git pull` both repos. They must sit as **siblings** (the compose build contexts are
   relative: `../../worldcup-2026`, `../../connections-score-analyzer`).
3. Rebuild + restart on the VPS:
   `docker compose -f docker/docker-compose.yml --profile gateway up -d --build worldcup picoclaw-gateway`
   (workspace MD/script changes are picked up via the bind mount; the `worldcup` image must be rebuilt).

Because the VPS is small, keep the Python image lean (`python:3.12-slim`, only `flask`+`requests`)
and the on-disk cache bounded.

## Notes / non-goals
- MVP relies on openfootball's update cadence for "live" results (semi-offline) — acceptable per the
  user; the `data_source.py` interface is the single seam to later swap in a paid live API.
- No changes to the existing Connections scoring flow; World Cup is purely additive.
- I will not push or rebuild — the user does that manually on the VPS. I'll flag when it's time.
