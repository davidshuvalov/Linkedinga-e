# LinkedIn Games WhatsApp Score Tracker

A WhatsApp bot that tracks LinkedIn game scores (Queens, Tango, Pinpoint,
Crossclimb, Zip) for a friend group of 6–12 people, posts daily recaps and
weekly leaderboards into the group, and allocates weekly prizes.

## Status

Phase 1 (repo scaffold + parsers + schema) — **in progress**. See the
"Build plan" section below for what's still pending.

## Stack

- Python 3.11+, FastAPI, Uvicorn
- Twilio WhatsApp Business API (sandbox while developing)
- Supabase (Postgres) for storage
- APScheduler for daily/weekly jobs
- Deploy target: Railway
- Timezone: `Australia/Sydney`

## Local development (Git Bash on Windows friendly)

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd Linkedinga-e

# 2. Create and activate a venv.
#    Git Bash on Windows: use the Scripts/activate path.
python -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash)
# source .venv/bin/activate     # macOS / Linux

# 3. Install deps
pip install -r requirements.txt

# 4. Copy env and fill in secrets
cp .env.example .env

# 5. Run the tests
pytest
```

## Database

Schema lives in [`db/schema.sql`](db/schema.sql). To apply it:

1. Open your Supabase project → **SQL Editor**.
2. Paste the contents of `db/schema.sql` and run.

Tables:

- `players(id, whatsapp_id, display_name, created_at)`
- `scores(id, player_id, game, puzzle_no, puzzle_date, raw_score, share_text, created_at)`
  with `UNIQUE(player_id, game, puzzle_no)` for dedup.
- `unparsed_messages(id, whatsapp_id, body, created_at)` — captures share-text
  that looked like a score but failed parsing, so we can tune regexes when
  LinkedIn changes their format.

`raw_score` convention:

| Game        | Unit                |
| ----------- | ------------------- |
| queens      | seconds (lower ↓)   |
| tango       | seconds (lower ↓)   |
| crossclimb  | seconds (lower ↓)   |
| zip         | seconds (lower ↓)   |
| pinpoint    | guess count 1–5 ↓   |

## Scoring rules

Rank-based per game per day:

- 1st: **5 points**
- 2nd: **3 points**
- 3rd: **1 point**
- Others: **0 points**

Ties share the better rank (standard competition ranking — two players tied
for 1st each get 5 points, and the next rank is 3rd).

Weekly total = sum of daily points across all 5 games. Weeks run **Monday to
Sunday** in `Australia/Sydney`.

## Weekly prize categories

- **Champion** — most total points.
- **All-rounder** — most distinct games played (tiebreak: points).
- **Streak king** — most days played (tiebreak: points).
- **Wooden spoon** — fewest points among participants.

## Build plan

- [x] Phase 1 — Core: repo scaffold, schema, parsers + unit tests
- [ ] Phase 2 — Webhook: FastAPI `/webhook`, Twilio payload handling, dedup
- [ ] Phase 3 — Scoring & recaps: `scoring.py`, `scheduler.py`, CLI
- [ ] Phase 4 — Scheduling & deploy: APScheduler, Railway config
- [ ] Phase 5 — Polish: `stats` and `unparsed` DM commands

## Known unknowns (help wanted)

The exact current share-text format for each of the 5 games. Phase 1 ships
with permissive first-draft regexes and placeholder fixtures — see `TODO`
comments in `app/parsers.py`. Paste a real share-text sample and we'll
tune + add a regression test.
