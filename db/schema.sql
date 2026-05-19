-- LinkedIn Games WhatsApp Score Tracker — Supabase / Postgres schema.
--
-- Apply via the Supabase SQL editor, or `psql` against your project:
--   psql "$SUPABASE_DB_URL" -f db/schema.sql
--
-- Safe to re-run: every statement uses IF NOT EXISTS.

-- ---------- groups ----------
-- A group is a self-contained leaderboard. Players sign up to a
-- group via `group <name>` (creates if new, joins if existing); their
-- scores, recaps, and per-day taunts are scoped to that group.
-- ``name_lower`` is the case-insensitive uniqueness key (computed
-- app-side as lower(name)) so members can spell the group however
-- they like in DMs and still hit the same row.
create table if not exists groups (
    id          bigserial primary key,
    name        text        not null,
    name_lower  text        not null unique,
    recap_to    text,
    created_at  timestamptz not null default now()
);

-- Seed a default group so existing players + scores have somewhere
-- to land during the backfill below. Idempotent — re-running is a
-- no-op once the row exists.
insert into groups (name, name_lower)
values ('default', 'default')
on conflict (name_lower) do nothing;

-- ---------- players ----------
create table if not exists players (
    id           bigserial primary key,
    whatsapp_id  text        not null unique,
    display_name text        not null,
    created_at   timestamptz not null default now()
);

-- Opt-out flag for daily/weekly recap DMs. Added after the initial
-- schema, so it's applied as an idempotent ALTER rather than baked
-- into the CREATE above — safe to re-run against existing databases.
alter table players
    add column if not exists notifications_enabled boolean
    not null default true;

-- ``group_id`` ties a player to their current group. Stays nullable
-- forever: a brand-new WhatsApp number creates a ``players`` row
-- before they've run ``group <name>``, and the webhook's onboarding
-- gate uses NULL as the "needs to pick a group first" signal.
alter table players
    add column if not exists group_id bigint references groups(id) on delete restrict;

-- Backfill any existing player rows into the default group so the
-- live friend group keeps DMing the bot without re-onboarding.
update players
set group_id = (select id from groups where name_lower = 'default')
where group_id is null;

create index if not exists players_group_id_idx on players (group_id);

-- ---------- scores ----------
-- raw_score convention:
--   queens/tango/crossclimb/zip/patches/mini_sudoku → seconds (lower is better)
--   pinpoint                                        → guess count 1..5 (lower is better)
create table if not exists scores (
    id          bigserial primary key,
    player_id   bigint      not null references players(id) on delete cascade,
    game        text        not null,
    puzzle_no   integer     not null check (puzzle_no > 0),
    puzzle_date date        not null,
    raw_score   integer     not null check (raw_score >= 0),
    share_text  text        not null,
    created_at  timestamptz not null default now(),
    unique (player_id, game, puzzle_no)
);

-- Allowed-games check, maintained separately so new games can be added
-- simply by re-running this file (drops + recreates the constraint). The
-- ``if exists`` guard lets this run cleanly against both fresh installs
-- and existing databases that used an earlier subset of games.
alter table scores drop constraint if exists scores_game_check;
alter table scores add constraint scores_game_check
    check (game in (
        'queens',
        'tango',
        'pinpoint',
        'crossclimb',
        'zip',
        'patches',
        'mini_sudoku'
    ));

create index if not exists scores_puzzle_date_idx on scores (puzzle_date);
create index if not exists scores_game_date_idx  on scores (game, puzzle_date);
create index if not exists scores_player_id_idx  on scores (player_id);

-- ``group_id`` scopes every leaderboard / recap / PB query to a single
-- group. Added nullable so existing rows can be backfilled, then
-- locked NOT NULL once everyone's on the default group. The unique
-- (player_id, game, puzzle_no) constraint above is intentionally
-- *not* group-scoped — a player can't legitimately submit the same
-- puzzle twice even if they switch groups.
alter table scores
    add column if not exists group_id bigint references groups(id) on delete restrict;

update scores
set group_id = (select id from groups where name_lower = 'default')
where group_id is null;

alter table scores alter column group_id set not null;

create index if not exists scores_group_date_idx
    on scores (group_id, puzzle_date);
create index if not exists scores_group_game_date_idx
    on scores (group_id, game, puzzle_date);
create index if not exists scores_group_game_score_idx
    on scores (group_id, game, raw_score);

-- ---------- unparsed_messages ----------
-- Stash anything that looked like a share but failed parsing, for debugging
-- / regex tuning when LinkedIn tweaks the share-text format.
create table if not exists unparsed_messages (
    id          bigserial primary key,
    whatsapp_id text        not null,
    body        text        not null,
    created_at  timestamptz not null default now()
);

create index if not exists unparsed_messages_created_at_idx
    on unparsed_messages (created_at desc);

-- ---------- recap_log ----------
-- Records each recap/wrap that's been sent, plus per-player per-day
-- cooldown markers for the brag/gripe Easter-egg broadcasts. Unique
-- constraint on (recap_date, recap_type) makes "has this fired?" a
-- single-row lookup.
--
-- The original schema gated ``recap_type`` to ('daily', 'weekly')
-- via a CHECK constraint. The taunt cooldown keys (``taunt:brag:42``
-- etc.) reuse this table to avoid creating a sibling table for what
-- amounts to "did this thing fire today?", so the constraint is
-- loosened to free-text. The comment below documents the value
-- vocabulary so old keys aren't surprising readers.
--
-- recap_type values currently in use:
--   'daily'                - scheduled daily recap (cron)
--   'weekly'               - scheduled weekly wrap (cron)
--   'taunt:brag:<id>'      - player <id> used `brag` today (cooldown)
--   'taunt:gripe:<id>'     - player <id> used `gripe` today (cooldown)
create table if not exists recap_log (
    id          bigserial primary key,
    recap_date  date not null,
    recap_type  text not null,
    sent_at     timestamptz not null default now(),
    unique (recap_date, recap_type)
);

-- Drop the legacy ``recap_type in ('daily','weekly')`` CHECK so
-- existing databases accept the new taunt cooldown keys. Idempotent:
-- IF EXISTS guards against rerunning on a fresh DB that never had
-- the constraint.
alter table recap_log drop constraint if exists recap_log_recap_type_check;

create index if not exists recap_log_date_idx on recap_log (recap_date);

-- Group-scope ``recap_log`` so per-group recaps don't collide on the
-- (recap_date, recap_type) unique. Backfill to the default group,
-- lock NOT NULL, drop the legacy unique constraint, replace with a
-- (group_id, recap_date, recap_type) unique.
alter table recap_log
    add column if not exists group_id bigint references groups(id) on delete restrict;

update recap_log
set group_id = (select id from groups where name_lower = 'default')
where group_id is null;

alter table recap_log alter column group_id set not null;

alter table recap_log
    drop constraint if exists recap_log_recap_date_recap_type_key;
alter table recap_log
    drop constraint if exists recap_log_group_date_type_key;
alter table recap_log
    add constraint recap_log_group_date_type_key
    unique (group_id, recap_date, recap_type);

create index if not exists recap_log_group_date_idx
    on recap_log (group_id, recap_date);

-- ``enabled_games`` is a JSON array of game keys that this group tracks,
-- e.g. '["queens","zip","tango"]'. NULL means the group inherits the
-- global default from app settings. Added after the initial schema so
-- it's applied as an idempotent ALTER — safe to re-run.
alter table groups
    add column if not exists enabled_games text;
