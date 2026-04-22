"""Command-line entrypoint for previewing recaps and wraps locally.

Usage (from the repo root)::

    python -m app.cli recap                 # today's daily recap
    python -m app.cli recap --date 2026-04-14
    python -m app.cli wrap                  # this week's wrap
    python -m app.cli wrap --week-of 2026-04-14
    python -m app.cli recap --demo          # seed an in-memory repo with
                                            # sample data and print

If ``SUPABASE_URL`` + ``SUPABASE_KEY`` are set, the CLI reads from Supabase.
Otherwise it falls back to an in-memory repository (empty unless ``--demo``
is passed), so you can iterate on formatting without a database.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import Optional, Sequence

from .config import load_settings
from .db import InMemoryRepository, Repository, SupabaseRepository
from .puzzles import week_bounds
from .scheduler import daily_recap, weekly_wrap


def _build_repository() -> Repository:
    settings = load_settings()
    if settings.has_supabase:
        from supabase import create_client  # type: ignore

        client = create_client(settings.supabase_url, settings.supabase_key)
        return SupabaseRepository(client)
    return InMemoryRepository()


def _seed_demo(repo: InMemoryRepository, *, today: Optional[date] = None) -> None:
    """Populate ``repo`` with deterministic sample data for visual preview.

    Seeds four players and a mix of scores across two days so the recap
    and weekly wrap both have interesting content — including tied scores,
    pinpoint guesses, and the multi-word ``Mini Sudoku`` game.
    """
    today = today or date.today()
    yesterday = today - timedelta(days=1)

    alice = repo.get_or_create_player("whatsapp:+61400000001", "Alice")
    bob = repo.get_or_create_player("whatsapp:+61400000002", "Bob")
    charlie = repo.get_or_create_player("whatsapp:+61400000003", "Charlie")
    dee = repo.get_or_create_player("whatsapp:+61400000004", "Dee")

    fakes = [
        # Today — mix of games, one tie, pinpoint + mini_sudoku + patches
        (alice.id, "queens", 714, today, 10),
        (bob.id, "queens", 714, today, 23),
        (charlie.id, "queens", 714, today, 45),
        (dee.id, "queens", 714, today, 60),
        (alice.id, "tango", 554, today, 35),
        (bob.id, "tango", 554, today, 35),  # tied for 1st
        (charlie.id, "tango", 554, today, 60),
        (alice.id, "pinpoint", 714, today, 3),
        (bob.id, "pinpoint", 714, today, 2),
        (dee.id, "patches", 28, today, 13),
        (alice.id, "mini_sudoku", 246, today, 76),
        (bob.id, "crossclimb", 714, today, 104),
        (alice.id, "crossclimb", 714, today, 95),
        # Yesterday
        (alice.id, "queens", 713, yesterday, 30),
        (bob.id, "queens", 713, yesterday, 20),
        (dee.id, "zip", 392, yesterday, 15),
        (charlie.id, "zip", 392, yesterday, 22),
    ]
    for pid, game, pno, pdate, raw in fakes:
        repo.insert_score(
            player_id=pid,
            game=game,
            puzzle_no=pno,
            puzzle_date=pdate,
            raw_score=raw,
            share_text=f"{game} #{pno} (demo)",
        )


def _get_repo_maybe_seeded(args: argparse.Namespace) -> Repository:
    if args.demo:
        repo = InMemoryRepository()
        _seed_demo(repo)
        return repo
    return _build_repository()


def cmd_recap(args: argparse.Namespace) -> int:
    repo = _get_repo_maybe_seeded(args)
    settings = load_settings()
    target = date.fromisoformat(args.date) if args.date else date.today()
    # daily_recap now takes the WHOLE week's scores so it can render the
    # running "Week so far" leaderboard at the bottom.
    monday, sunday = week_bounds(target)
    scores = repo.list_scores(date_from=monday, date_to=sunday)
    sys.stdout.write(daily_recap(target, scores, settings.enabled_games))
    return 0


def cmd_wrap(args: argparse.Namespace) -> int:
    repo = _get_repo_maybe_seeded(args)
    settings = load_settings()
    ref = date.fromisoformat(args.week_of) if args.week_of else date.today()
    start, end = week_bounds(ref)
    scores = repo.list_scores(date_from=start, date_to=end)
    sys.stdout.write(weekly_wrap(start, end, scores, settings.enabled_games))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    rp = sub.add_parser("recap", help="Print the daily recap")
    rp.add_argument("--date", help="YYYY-MM-DD (default: today)")
    rp.add_argument(
        "--demo",
        action="store_true",
        help="Use a fresh in-memory repo seeded with sample data",
    )
    rp.set_defaults(func=cmd_recap)

    wp = sub.add_parser("wrap", help="Print the weekly wrap")
    wp.add_argument(
        "--week-of",
        help="YYYY-MM-DD within the target week (default: today)",
    )
    wp.add_argument(
        "--demo",
        action="store_true",
        help="Use a fresh in-memory repo seeded with sample data",
    )
    wp.set_defaults(func=cmd_wrap)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
