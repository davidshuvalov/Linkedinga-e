# LinkedIn Games WhatsApp Score Tracker

A WhatsApp bot that tracks LinkedIn game scores (Queens, Tango, Pinpoint,
Crossclimb, Zip, Patches, and Mini Sudoku) for a friend group of 6–12
people, posts daily recaps and weekly leaderboards into the group, and
allocates weekly prizes.

## Status

Phase 3 (scoring, recaps, preview CLI) complete. See the "Build plan"
section below for what's still pending.

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

# 6. Run the webhook locally
uvicorn app.main:app --reload
```

With no Supabase credentials set, the app boots against an in-memory
repository so you can smoke-test the webhook end-to-end without a database.

### Simulating Twilio webhook POSTs

With `uvicorn` running, in a second Git Bash terminal:

```bash
./scripts/curl_webhook.sh
```

That posts form-encoded payloads (`From`, `Body`, `ProfileName`) that mirror
what Twilio sends, and prints each TwiML reply. To post a single custom
message:

```bash
curl -s -X POST http://127.0.0.1:8000/webhook \
    -d 'From=whatsapp:+61400000001' \
    -d 'ProfileName=Alice' \
    --data-urlencode 'Body=Queens #365 | 1:23'
```

### Previewing recaps and wraps from the CLI

Phase 3 ships a CLI entrypoint so you can preview the daily recap or
weekly wrap without waiting for the scheduled job (or even without a
database — pass `--demo` to use a fresh in-memory repo seeded with
sample data):

```bash
# Today's recap from Supabase (or empty if no data yet / no creds set)
python -m app.cli recap

# Today's recap against seeded demo data — no database required
python -m app.cli recap --demo

# A specific day's recap
python -m app.cli recap --date 2026-04-14

# This week's wrap (Mon–Sun, Sydney tz)
python -m app.cli wrap

# A specific week's wrap (any date inside the week works)
python -m app.cli wrap --week-of 2026-04-15 --demo
```

Daily grouping in the scoring logic uses `(game, puzzle_no)` rather
than the stored `puzzle_date`, so a shared daily round is scored as a
single head-to-head even when two players' submissions straddle
midnight and land on different `puzzle_date` values.

### Wiring up the real Twilio sandbox

1. Enable the WhatsApp sandbox in the Twilio console.
2. Expose your local server with `ngrok http 8000` (or similar).
3. In **Twilio Console → Messaging → Sandbox settings**, set the *"When a
   message comes in"* URL to `https://<ngrok-id>.ngrok.io/webhook`, method `POST`.
4. From your phone, join the sandbox and send `Queens #1 | 0:30` — you should
   get a reply and a row in `scores`.

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

| Game         | Unit                |
| ------------ | ------------------- |
| queens       | seconds (lower ↓)   |
| tango        | seconds (lower ↓)   |
| crossclimb   | seconds (lower ↓)   |
| zip          | seconds (lower ↓)   |
| patches      | seconds (lower ↓)   |
| mini_sudoku  | seconds (lower ↓)   |
| pinpoint     | guess count 1–5 ↓   |

## Scoring rules

Rank-based per game per day:

- 1st: **5 points**
- 2nd: **3 points**
- 3rd: **1 point**
- Others: **0 points**

Ties share the better rank (standard competition ranking — two players tied
for 1st each get 5 points, and the next rank is 3rd).

Weekly total = sum of daily points across all 7 games. Weeks run **Monday to
Sunday** in `Australia/Sydney`.

## Weekly prize categories

- **Champion** — most total points.
- **All-rounder** — most distinct games played (tiebreak: points).
- **Streak king** — most days played (tiebreak: points).
- **Wooden spoon** — fewest points among participants.

## Build plan

- [x] Phase 1 — Core: repo scaffold, schema, parsers + unit tests
- [x] Phase 2 — Webhook: FastAPI `/webhook`, Twilio payload handling, dedup
- [x] Phase 3 — Scoring & recaps: `scoring.py`, `scheduler.py`, CLI
- [ ] Phase 4 — Scheduling & deploy: APScheduler, Railway config
- [ ] Phase 5 — Polish: `stats` and `unparsed` DM commands

## Share-text format regression fixtures

Real share-text samples for all 7 games (captured 2026-04) are pinned in
`tests/test_parsers.py::TestRealSamples`. If LinkedIn ever tweaks the
format, update both the regex in `app/parsers.py` and the fixture
together — the class exists specifically as the regression net.
