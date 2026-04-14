-- LinkedIn Games WhatsApp Score Tracker — Supabase / Postgres schema.
--
-- Apply via the Supabase SQL editor, or `psql` against your project:
--   psql "$SUPABASE_DB_URL" -f db/schema.sql
--
-- Safe to re-run: every statement uses IF NOT EXISTS.

-- ---------- players ----------
create table if not exists players (
    id           bigserial primary key,
    whatsapp_id  text        not null unique,
    display_name text        not null,
    created_at   timestamptz not null default now()
);

-- ---------- scores ----------
-- raw_score convention:
--   queens/tango/crossclimb/zip → seconds (lower is better)
--   pinpoint                    → guess count 1..5 (lower is better)
create table if not exists scores (
    id          bigserial primary key,
    player_id   bigint      not null references players(id) on delete cascade,
    game        text        not null
        check (game in ('queens','tango','pinpoint','crossclimb','zip')),
    puzzle_no   integer     not null check (puzzle_no > 0),
    puzzle_date date        not null,
    raw_score   integer     not null check (raw_score >= 0),
    share_text  text        not null,
    created_at  timestamptz not null default now(),
    unique (player_id, game, puzzle_no)
);

create index if not exists scores_puzzle_date_idx on scores (puzzle_date);
create index if not exists scores_game_date_idx  on scores (game, puzzle_date);
create index if not exists scores_player_id_idx  on scores (player_id);

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
