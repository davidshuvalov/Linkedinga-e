# LinkedIn Games WhatsApp Score Tracker

A WhatsApp bot that tracks LinkedIn game scores (Queens, Tango, Pinpoint,
Crossclimb, Zip, Patches, and Mini Sudoku) for a friend group of 6–12
people, posts daily recaps and weekly leaderboards into the group, and
allocates weekly prizes.

## Status

All 5 phases complete. The bot is ready to deploy.

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
single head-to-head even when two players' submissions straddle the
LA-midnight rollover. Because submissions are also validated against
the currently-live puzzle number (see "Puzzle-number validation"
below), a single round only ever carries one puzzle_no anyway.

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
- 2nd: **4 points**
- 3rd: **3 points**
- 4th: **2 points**
- 5th: **1 point**
- 6th+: **0 points**

Ties: average the position points the tied players would fill, then round
**up** (`math.ceil`). Examples:

| Tie scenario       | Calculation            | Each gets |
| ------------------ | ---------------------- | --------- |
| Tied 1st/2nd       | ceil((5+4)/2) = 4.5    | **5**     |
| Tied 2nd/3rd       | ceil((4+3)/2) = 3.5    | **4**     |
| Tied 1st/2nd/3rd   | ceil((5+4+3)/3) = 4.0  | **4**     |
| Tied 4th/5th       | ceil((2+1)/2) = 1.5    | **2**     |

Weekly total = sum of daily points across **enabled** games only. Weeks
run **Monday to Sunday in LA time**, matching LinkedIn's puzzle days.

## Puzzle-number validation

LinkedIn rolls a new puzzle for each game at **midnight US Pacific**,
honouring US DST. The previous day's puzzle expires at the same moment.
The bot enforces "one round at a time" by validating every submission
against the currently-live puzzle number — anything stale (yesterday's)
or future (tomorrow's / a typo) is rejected with a friendly explanation
and **not** recorded.

How it works: `app/puzzles.py` pins a per-game epoch (puzzle number live
on 2026-04-22 LA). `expected_puzzle_no(game, now)` returns today's
expected number by adding the LA-day delta. `zoneinfo` handles DST.

Example rejection:

> That's Zip #407 (a future day's puzzle). Today's Zip is #401 — I can
> only record today's scores. (LinkedIn resets at midnight US Pacific.)

To advance the epoch (e.g. if LinkedIn skips a number): update the
tuple in `PUZZLE_EPOCH` — no other change required.

### Game toggle

Not all 7 games need to be tracked. Set the `ENABLED_GAMES` env var to a
comma-separated list. Default:

```
ENABLED_GAMES=queens,tango,zip,patches,mini_sudoku
```

Pinpoint and Crossclimb are **off by default**. Scores for disabled games
are still parsed and stored (so historical data is preserved if you
re-enable them later), but they don't appear in recaps, wraps, or the
leaderboard. The webhook reply adds a note when a disabled game is
submitted.

## Weekly prize categories

The wrap shows a compact leaderboard (one line per player, sorted by
total points) followed by three prizes. Champion / Wooden spoon were
dropped because the top and bottom of the leaderboard already name
them; All-rounder is visible as the `(N games)` suffix on each row.

- **Most firsts** — most 1st-place finishes in **competitive rounds
  (≥2 players)**. Tied-for-1st credits every tied player. Singleton
  rounds (where only one person submitted a game) don't count — you
  need someone to beat.
- **Most lasts** — the flip side. Most last-place finishes in
  competitive rounds. Ties at the bottom credit everyone tied.
  Tiebreak goes to the player with *lower* total points, so being
  weaker overall owns the title.
- **Best average** — highest points-per-submission, with a
  minimum-submissions floor (`MIN_SUBMISSIONS_FOR_AVERAGE_PRIZE` in
  `app/scoring.py`, default 5). Prevents someone winning on one lucky
  round. If nobody clears the floor, the line is suppressed.

## Build plan

- [x] Phase 1 — Core: repo scaffold, schema, parsers + unit tests
- [x] Phase 2 — Webhook: FastAPI `/webhook`, Twilio payload handling, dedup
- [x] Phase 3 — Scoring & recaps: `scoring.py`, `scheduler.py`, CLI
- [x] Phase 4 — Scheduling & deploy: APScheduler, Railway config
- [x] Phase 5 — Polish: `stats` and `unparsed` DM commands

## Deploying to Railway

1. Push this repo to a GitHub repository connected to Railway.
2. In Railway's dashboard, set the following environment variables:
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`
   - `TWILIO_RECAP_TO` — **leave unset** for DM-only delivery
     (recommended for the Twilio sandbox; see "Onboarding your friends"
     below). Only set this if you've graduated to a Meta-approved
     Business sender and you have a real group JID.
   - `SUPABASE_URL` — bare project origin, no trailing slash, no
     `/rest/v1` suffix (e.g. `https://<ref>.supabase.co`)
   - `SUPABASE_KEY` — use the **`service_role`** key (Settings → API).
     It bypasses RLS, so the bot works regardless of whether you've
     enabled row-level security later.
   - `APP_TIMEZONE` (default: `Australia/Sydney`) — only affects the
     `now` used by the webhook log line; the scheduler runs in LA time
     regardless.
3. Railway auto-detects `Procfile` or `railway.toml`; it will run:
   ```
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```
4. Set your Twilio webhook URL to
   `https://<your-railway-app>.up.railway.app/webhook` (method: POST).
5. Health check is at `GET /health`.

### Scheduled jobs

A single APScheduler cron runs inside the same process as the web
server, fired at the **LinkedIn puzzle rollover** so the message
always lands moments before the next day's puzzle drops:

| Job                  | Schedule                | Timezone             |
| -------------------- | ----------------------- | -------------------- |
| Daily recap / wrap   | Every day at 00:00      | America/Los_Angeles  |

The job recaps the LA day that just closed. **On Mon–Sat (LA)** that's
the regular daily recap (per-game rankings + running "Week so far"
leaderboard). **On Sunday (LA)** — which lands Monday afternoon
Sydney time — it instead emits the full **weekly wrap** (Sunday's
per-game rankings + final week totals + per-game weekly winners +
three prizes) so the entire end-of-week roundup arrives in one
forwardable message.

In Sydney that means:

- Apr–Oct (AEST + PDT): ~**5pm Sydney every day**.
- Oct–Nov (AEDT + PDT): ~**6pm Sydney**.
- Nov–Apr (AEDT + PST): ~**7pm Sydney**.

Delivery: if `TWILIO_RECAP_TO` is set, the bot tries the group post
first and falls back to per-player DMs on failure. If it's **unset**
(the default for sandbox setups), the bot goes straight to DMing every
player who submitted a score that period. When `TWILIO_ACCOUNT_SID` is
missing entirely (local dev), the recap is printed to stdout instead.

## Onboarding your friends

The bot sends score confirmations, error messages, and daily/weekly
recaps as **direct messages** — not posts into your group. (Posting
into a WhatsApp group requires a real group JID; DM-only is simpler
and is what the rest of this section assumes.)

### The one-time setup (each friend does this, business sender)

1. Each friend saves your **business WhatsApp number** as a contact on
   their phone.
2. From WhatsApp, they send any message to that number — even just
   `hi`. This opens WhatsApp's 24-hour customer-care window so the bot
   can reply with free-form text.
3. From now on they DM their LinkedIn share text to that number —
   **not** into the friends' group chat. Example share:
   ```
   Queens #722
   1:05
   ```

No join phrase, no 72-hour expiry — those are sandbox-only quirks (see
below). Each friend just needs to message the bot first.

### Heads up: WhatsApp's 24-hour window

WhatsApp only lets a business send free-form messages to a user within
24 hours of that user's last inbound message. After that window
closes, only **pre-approved templates** can go out. In practice:

- **Score confirmations** — always fine; the friend just messaged you.
- **Daily recap / weekly wrap / morning nudge** — fine for anyone who
  submitted a score in the last 24h. Friends who skip a day will
  silently miss that day's recap. If that matters, register an
  approved template in Twilio Console and route the recap through it;
  if not (people who play daily stay in the window), ignore it.

### Sandbox onboarding (development only)

If you're testing against the Twilio WhatsApp sandbox instead of a
production business sender, the flow is stricter:

1. Each friend saves the **Twilio sandbox number** (Twilio Console →
   Messaging → Try it out → Send a WhatsApp message) as a contact.
2. They DM the **join phrase** (two words unique to your Twilio
   account, e.g. `join cozy-otter`) once. Twilio replies "Connected to
   sandbox."
3. Sandbox connections expire after 72 hours of silence — they'll need
   to re-send the join phrase if they go quiet for a weekend.

### What the friends see in practice

- They keep chatting in the human WhatsApp group as normal.
- Scores are DM'd 1:1 to the bot. Bot replies "Got it, …" or rejects
  stale / future puzzle numbers.
- Daily recap (~5pm Sydney, drifting to 7pm Sydney through the year)
  lands in each friend's 1:1 chat with the bot, listing the day's
  rankings per game. They can screenshot and paste into the group if
  they want.
- Weekly wrap lands the same way on Monday evenings Sydney time —
  deliberately short (header + the five prizes, ~6 lines total) so
  it's easy to forward into the group chat as a single message.

### Pinned message template for your group

Paste something like this into your group chat so friends know the
drill:

> **LinkedIn Games bot setup** 🎯
> 1. Save this number: *\<your business WhatsApp number\>*
> 2. Send it any message once (e.g. `hi`) to start.
> 3. After each game, DM your share text to the bot — don't post it in
>    this group.
> 4. You'll get a daily recap in your DM each evening.

## Bot commands

Players can DM the bot (case-insensitive) with these keywords:

**Look at scores**

> **No-peek gate (LinkedIn-style):** `recap` / `today` and `leaderboard`
> hide today's results until you've made an attempt yourself.
> - 0 of today's games submitted → blocked entirely.
> - Some submitted → recap shows only the games you've played; the
>   leaderboard stays locked.
> - All submitted → full view.
>
> Past-day commands (`yesterday`, `recap YYYY-MM-DD`,
> `leaderboard yesterday`, `wrap`, `all`, `month`, `year`, etc.) are
> never gated.

| Command                                      | Response |
| -------------------------------------------- | -------- |
| `recap` / `today`                            | Daily recap for today (LA) — per-game rankings + "Week so far" leaderboard + passive-aggressive nudge for anyone ghosting. Gated; see above. |
| `yesterday`                                  | Yesterday's recap. |
| `N days ago` (1–6)                           | Recap for N days back. |
| `recap YYYY-MM-DD`                           | Recap for a specific date within the last 6 days. |
| `week` / `wrap`                              | Weekly wrap — per-game winners + prizes. |
| `all` / `history`                            | Every round day-by-day, Mon → today. |
| `leaderboard` / `standings`                  | Just the weekly leaderboard. Gated; see above. |
| `leaderboard <game>` (e.g. `leaderboard queens`) | Per-game weekly standings. Gated; see above. |
| `prizes`                                     | Live snapshot of who's winning each prize. |
| `missing` / `who` / `ghosts`                 | Players who played earlier this week but not today. |
| `games` / `enabled`                          | Which games are tracked vs parsed-but-untracked. |
| `rules` / `scoring`                          | How points are calculated. |

**About you**

| Command            | Response |
| ------------------ | -------- |
| `stats`            | Your all-time per-game stats + personal bests. |
| `pb` / `bests`     | Just personal bests (subset of `stats`). |
| `streak`           | Your current consecutive-days streak. |
| `vs <name>`        | Head-to-head, all-time, vs the named opponent. |

**Change things**

| Command            | Response |
| ------------------ | -------- |
| `undo`             | Delete your most recent submission for today (LA). Older days are locked. |
| `name <new>`       | Change your display name (overrides WhatsApp profile name). |
| `notify on` / `off` | Opt in/out of daily recap DMs. Scores still accepted when off. |
| `help` / `?`       | Show the full command list. |
| `unparsed`         | Last 10 unparsed messages (admin debugging). |

**Easter eggs** (once each per day per sender)

| Command                  | Response |
| ------------------------ | -------- |
| `brag` / `flex`          | DM every other recently-active player a "crushing it" line, and reply to you with the count + a preview of what got sent. |
| `gripe` / `whinge`       | Same shape as `brag` but a self-deprecating "today's a write-off" line. |
| `nag` / `blast` / `poke` | Manual fan-out of the morning nudge — DMs every active player who hasn't finished today's games, excluding you. Reply lists who got nudged. |

**Unrecognised messages** (anything that isn't a command and isn't a
score share) now get a "I didn't understand that" reply plus the help
blurb. The bot operates in 1:1 DMs so silence was leaving users
guessing. Empty / whitespace-only sends stay silent.

## Share-text format regression fixtures

Real share-text samples for all 7 games (captured 2026-04) are pinned in
`tests/test_parsers.py::TestRealSamples`. If LinkedIn ever tweaks the
format, update both the regex in `app/parsers.py` and the fixture
together — the class exists specifically as the regression net.
