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
LA-midnight rollover — or when one of them pastes their share a few
days late (see "Puzzle numbers, and submitting a day late" below).
The puzzle number is the round's identity; `puzzle_date` is derived
from it, so a round only ever carries one puzzle_no.

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

The file is **idempotent** — every statement uses `IF NOT EXISTS`,
backfill `UPDATE`s, or guarded `DROP CONSTRAINT IF EXISTS`. Re-running
it on a populated database is safe.

Tables:

- `groups(id, name, name_lower, recap_to, created_at)` — each group is
  a self-contained leaderboard. `name_lower` is the case-insensitive
  uniqueness key. `recap_to` is an optional per-group WhatsApp group
  post target (overrides `TWILIO_RECAP_TO` for that group).
- `players(id, whatsapp_id, display_name, group_id, notifications_enabled, created_at)`
  — `group_id` references the player's *current* group. Stays nullable;
  brand-new players have `group_id IS NULL` until they run `group <name>`.
- `scores(id, player_id, group_id, game, puzzle_no, puzzle_date, raw_score, share_text, created_at)`
  with `UNIQUE(player_id, game, puzzle_no)` for dedup. `group_id` is
  set at insert time and never moved — switching groups doesn't
  rewrite history.
- `recap_log(id, group_id, recap_date, recap_type, sent_at)` with
  `UNIQUE(group_id, recap_date, recap_type)` so per-group recap
  idempotency doesn't collide across groups.
- `teams(id, group_id, name, name_lower, created_at)` with
  `UNIQUE(group_id, name_lower)` — a team is a named subset of one
  group, so two groups can each run a team called "Reds".
- `team_members(id, team_id, group_id, player_id, created_at)` with
  `UNIQUE(group_id, player_id)` — that unique is what enforces one
  team per player per group, so adding someone to a second team
  moves them rather than double-counting their points.
- `unparsed_messages(id, whatsapp_id, body, created_at)` — captures share-text
  that looked like a score but failed parsing, so we can tune regexes when
  LinkedIn changes their format.

### Migrating an existing single-group deployment

The migration is automatic — running the updated `db/schema.sql` will:

1. Create the `groups` table and seed a row called `default`.
2. Add `group_id` to `players`, `scores`, and `recap_log`.
3. Backfill every existing row to the `default` group.
4. Lock `scores.group_id` and `recap_log.group_id` to NOT NULL.

Existing players don't re-onboard — they're already attached to the
default group, so their next message lands in the default group's
leaderboard exactly as before. To rename the default group later:

```sql
update groups set name = 'My Crew', name_lower = 'my crew'
where name_lower = 'default';
```

To wire a per-group WhatsApp group post target (instead of the global
`TWILIO_RECAP_TO`):

```sql
update groups set recap_to = 'whatsapp:+...' where name_lower = '...';
```

`raw_score` convention:

| Game         | Unit                |
| ------------ | ------------------- |
| queens       | seconds (lower ↓)   |
| tango        | seconds (lower ↓)   |
| crossclimb   | seconds (lower ↓)   |
| zip          | seconds (lower ↓)   |
| patches      | seconds (lower ↓)   |
| mini_sudoku  | seconds (lower ↓)   |
| wend         | seconds (lower ↓)   |
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

## Puzzle numbers, and submitting a day late

LinkedIn rolls a new puzzle for each game at **midnight US Pacific**,
honouring US DST. The previous day's puzzle expires at the same moment.

How it works: `app/puzzles.py` pins a per-game epoch (puzzle number live
on 2026-04-22 LA). `expected_puzzle_no(game, now)` returns today's
expected number by adding the LA-day delta. `zoneinfo` handles DST.

The gap between the number you paste and the live one **is** how many
days back your score belongs, which is what makes late submissions
work: forgot to send yesterday's Queens? Paste it today and it's filed
under yesterday, scored against the players you actually played
against.

> Got it, Alice. Queens #723: 1:20. Filed under Thu 23 Apr (yesterday).

Limits (`MAX_BACKDATE_DAYS = 7`):

- **Up to 7 days back.** Older than that and it's refused — reopening
  a month-old leaderboard isn't worth it.
- **Future numbers are always refused.** LinkedIn hasn't served that
  puzzle yet, so the score can't exist.
- **One submission per puzzle, still.** The existing
  `UNIQUE(player_id, game, puzzle_no)` dedup applies to late shares
  too, so you can't quietly re-submit yesterday with a better time.
- If that day's recap has already gone out, the confirmation says so —
  the standings the group saw have just moved.

Two side-effects are deliberately skipped for a backdated score,
because they describe a *live* round: the group photo-finish /
comeback broadcasts, and the early-fire recap. Personal notifications
still fire, minus the present-tense ones ("first on the board today"),
which would read as nonsense days later.

Example rejections:

> That's Zip #407 (tomorrow's puzzle). Today's Zip is #401 — I can't
> record a score for a puzzle that hasn't dropped yet.

> That's Queens #716, 8 days old. Today's is #724 — I can only take
> scores from the last 7 days.

To advance the epoch (e.g. if LinkedIn skips a number): update the
tuple in `PUZZLE_EPOCH` — no other change required.

### Game toggle

Not all 8 games need to be tracked. Set the `ENABLED_GAMES` env var to a
comma-separated list. Default:

```
ENABLED_GAMES=queens,tango,zip,patches,mini_sudoku
```

Pinpoint, Crossclimb, and Wend are **off by default**. Scores for disabled games
are still parsed and stored (so historical data is preserved if you
re-enable them later), but they don't appear in recaps, wraps, or the
leaderboard. The webhook reply adds a note when a disabled game is
submitted.

A group can override the list for itself with the `track` command —
`track queens tango zip patches mini_sudoku wend` picks up Wend without
touching `ENABLED_GAMES` or any other group. **The tracked list is the
whole definition of a day**: it decides what scores, and equally what
the bot waits for. Adding a game means the "day done" scorecard and the
early-fire group recap both hold until that game is in, and the morning
/ pre-reset nags keep asking for it.

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

### Message length

The daily recap is long — with 5 games and 6 players the per-game
rankings alone run ~35 lines, and the team section adds one row per
team on top of that.

A folded one-line-per-game layout was tried to fix that:

```
Queens #702: Alice 0:51 (5) · Bob 1:04 (4) · Carol 1:17 (3) · ...
```

It cut the recap from 62 lines to 28, and was **reverted**: at six
players the line wraps into a dense run of names and interpuncts, and
picking your own result out of it takes real effort. The per-game
rankings are the part people actually read, so they keep the room —

```
Queens #702
  Alice — 0:51 (5 pts)
  Bob — 1:04 (4 pts)
```

If the recap needs shortening again, cut a *block* rather than
compressing this one. `Game standings (week)` is the obvious
candidate: it's a top-3-per-game summary that the `Week so far`
leaderboard and the `leaderboard <game>` command already cover.

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

**Groups**

Every player belongs to exactly one group. Scores, recaps, leaderboards,
nudges, and taunt broadcasts are scoped per-group — players in
different groups don't see each other's data. A brand-new player has
to run `group <name>` before any other command works.

The first DM from a brand-new WhatsApp number — anything from "hi" to
a score share — gets a **welcome intro**: a short tour of how groups
work, an example score share, and the notification rhythm to expect.
Once they've joined, that intro is replaced by the regular command
dispatch.

| Command           | Response |
| ----------------- | -------- |
| `group <name>`    | Create a new group with that name **or** join an existing one (case-insensitive lookup). Onboards the sender so the rest of the commands unlock. |
| `switch <name>`   | Move to a different existing group. Errors with a hint to use `group <name>` if the target doesn't exist. **Past scores stay in the group they were earned in** — switching is forward-only. |

**Teams**

A team is a named subset of a group whose members' points are **added
together** into a single standing. Teams are optional — a group with
no teams behaves exactly as before, and the team block simply doesn't
appear anywhere.

Each player is on at most one team per group, so adding someone to a
second team *moves* them rather than counting their points twice.
Teams are group-scoped: two groups can each have a "Reds" without
colliding, and you can't put someone from another group on your team.

| Command | Response |
| ------- | -------- |
| `team <name>: <player>, <player>` | Create a team with those players. If the team already exists, adds them to it. Names are matched case-insensitively against your group's players; `me` means you. Every name has to resolve or nothing is written. |
| `team add <name>: <player>, ...` | Add players to an **existing** team. Errors if the team doesn't exist (the strict sibling of the create-or-add form, same as `switch` vs `group`). |
| `team remove <player>, ...` | Take players off whatever team they're on. Scores are untouched. |
| `team delete <name>` / `team disband <name>` | Disband a team. Every score stays exactly where it was. |
| `teams` | List this group's teams, their rosters, and anyone not on a team. |
| `team <name>` | One team's roster. |
| `team leaderboard` / `team standings` | Combined team standings for the week — team rows on top, the player rows they're built from underneath. Anyone with points but no team gets a "Not on a team" line so their score doesn't just vanish. |
| `team leaderboard <game>` | Same, restricted to one game (e.g. `team leaderboard queens`). |
| `team today` / `team day` | Just today's team scores. |
| `team month` / `team year` | Same, over the month or year to date. |

Separators are flexible: `team Reds: Alice, Bob`, `team Reds: Alice and
Bob`, and `team Reds Alice Bob` all work. Use the colon form for team
names with spaces in them.

A team's total is the plain sum of its members' points on the ordinary
player leaderboard — there's no separate scoring path, so a bigger
roster is a real advantage.

Once a group has at least one team, the standings table in the daily
recap, the weekly wrap, and the `leaderboard` command splits into two
sections — **Teams** above **Players** — instead of one flat list:

```
Week so far:
  Teams:
    1. Reds: 36 pts (T: 8:44, G:8) · today +18
    2. Blues: 12 pts (T: 5:28, G:4) · today +6
  Players:
    1. Alice: 20 pts (T: 4:00, G:4)
    2. Bob: 16 pts (T: 4:44, G:4)
    3. Carol: 12 pts (T: 5:28, G:4)
  Not on a team: Dave
```

Both sections use the same row format — points, `T:` cumulative
seconds across time-based games, `G:` rounds played — so a team reads
as just another competitor. Teams go first because for a group playing
in teams the team result is the headline and the per-player rows are
the detail behind it. Both are folded out of the *same* leaderboard,
so a team's total is always exactly the rows beneath it added up.

The `· today +N` tail is the team's score for that day alone: the
total answers "how's the week going", the tail answers "what did we
put on the board today". `team today` pulls the day's board on its
own. Groups with no teams see no change anywhere — same flat table as
before.

`team leaderboard` sits behind the same no-peek gate as the player
leaderboard (see below); `teams` and `team <name>` don't, since a
roster carries no scores.

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
| `track <game> ...` | Set which games your group plays. They score, and the daily wrap waits for all of them. `track all` / `track reset` for every game / the global default. |
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
