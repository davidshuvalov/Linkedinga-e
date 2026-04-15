"""Recap/wrap text builders.

:func:`daily_recap` and :func:`weekly_wrap` are pure functions that take a
batch of :class:`~app.db.ScoreRow` records and return a WhatsApp-friendly
formatted string. The Phase 4 APScheduler wiring will call these and hand
the result to Twilio; the Phase 3 CLI uses them directly for manual preview.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Sequence, Tuple

from .db import ScoreRow
from .scoring import (
    PlayerWeeklyStats,
    Prizes,
    assign_daily_points,
    prize_allocations,
    weekly_leaderboard,
)

_GAME_DISPLAY = {
    "queens": "Queens",
    "tango": "Tango",
    "pinpoint": "Pinpoint",
    "crossclimb": "Crossclimb",
    "zip": "Zip",
    "patches": "Patches",
    "mini_sudoku": "Mini Sudoku",
}

# The order games appear in recap output. Stable so that two recaps for
# different days render the same sections in the same places.
_GAME_ORDER: Tuple[str, ...] = (
    "queens",
    "tango",
    "crossclimb",
    "zip",
    "pinpoint",
    "patches",
    "mini_sudoku",
)


def _pts(points: int) -> str:
    return "1 pt" if points == 1 else f"{points} pts"


def _format_raw_score(game: str, raw: int) -> str:
    if game == "pinpoint":
        noun = "guess" if raw == 1 else "guesses"
        return f"{raw} {noun}"
    minutes, seconds = divmod(raw, 60)
    return f"{minutes}:{seconds:02d}"


# ---------------------------------------------------------------------------
# daily_recap
# ---------------------------------------------------------------------------


def daily_recap(day: date, scores: Sequence[ScoreRow]) -> str:
    """Format a daily recap string for a single ``day``.

    Scores whose ``puzzle_date`` doesn't match ``day`` are ignored — that
    way callers can pass a wider fetch (e.g. the whole week) and still get
    a clean per-day report.
    """
    header = f"Daily recap — {day.strftime('%a %d %b %Y')}"
    day_scores = [s for s in scores if s.puzzle_date == day]

    if not day_scores:
        return f"{header}\n\nNo scores yet.\n"

    groups: Dict[Tuple[str, int], List[ScoreRow]] = {}
    for s in day_scores:
        groups.setdefault((s.game, s.puzzle_no), []).append(s)

    lines: List[str] = [header, ""]
    day_totals: Dict[int, int] = {}
    player_names: Dict[int, str] = {}

    for game in _GAME_ORDER:
        matching = sorted(
            (key for key in groups if key[0] == game),
            key=lambda k: k[1],
        )
        for key in matching:
            group_scores = sorted(groups[key], key=lambda s: s.raw_score)
            points_map = assign_daily_points(group_scores)

            lines.append(f"{_GAME_DISPLAY[game]} #{key[1]}")
            for s in group_scores:
                player_names[s.player_id] = s.player_name
                pts = points_map[s.player_id]
                day_totals[s.player_id] = day_totals.get(s.player_id, 0) + pts
                lines.append(
                    f"  {s.player_name} — "
                    f"{_format_raw_score(game, s.raw_score)} ({_pts(pts)})"
                )
            lines.append("")

    lines.append("Day totals:")
    sorted_totals = sorted(
        day_totals.items(),
        key=lambda kv: (-kv[1], kv[0]),
    )
    for pid, pts in sorted_totals:
        lines.append(f"  {player_names[pid]}: {_pts(pts)}")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# weekly_wrap
# ---------------------------------------------------------------------------


def _format_stats_line(rank: int, p: PlayerWeeklyStats) -> str:
    games_word = "game" if p.distinct_games == 1 else "games"
    days_word = "day" if p.days_played == 1 else "days"
    return (
        f"  {rank}. {p.player_name}: {_pts(p.total_points)} "
        f"({p.distinct_games} {games_word}, {p.days_played} {days_word})"
    )


def weekly_wrap(
    week_start: date,
    week_end: date,
    scores: Sequence[ScoreRow],
) -> str:
    """Format a weekly wrap covering ``[week_start, week_end]`` inclusive.

    Expects Monday–Sunday bounds (the CLI / scheduler is responsible for
    computing them), but doesn't enforce this so the same function can be
    used for ad-hoc ranges during debugging.
    """
    header = (
        f"Weekly wrap — "
        f"{week_start.strftime('%a %d %b')} to "
        f"{week_end.strftime('%a %d %b %Y')}"
    )
    week_scores = [s for s in scores if week_start <= s.puzzle_date <= week_end]

    if not week_scores:
        return f"{header}\n\nNo scores this week.\n"

    lb = weekly_leaderboard(week_scores)
    prizes = prize_allocations(lb)

    lines: List[str] = [header, "", "Leaderboard:"]
    for i, p in enumerate(lb, start=1):
        lines.append(_format_stats_line(i, p))

    lines.append("")
    lines.append("Prizes:")

    if prizes.champion is not None:
        lines.append(
            f"  Champion: {prizes.champion.player_name} "
            f"({_pts(prizes.champion.total_points)})"
        )
    if prizes.all_rounder is not None:
        games_word = (
            "game" if prizes.all_rounder.distinct_games == 1 else "games"
        )
        lines.append(
            f"  All-rounder: {prizes.all_rounder.player_name} "
            f"({prizes.all_rounder.distinct_games} {games_word})"
        )
    if prizes.streak_king is not None:
        days_word = "day" if prizes.streak_king.days_played == 1 else "days"
        lines.append(
            f"  Streak king: {prizes.streak_king.player_name} "
            f"({prizes.streak_king.days_played} {days_word})"
        )
    if prizes.wooden_spoon is not None:
        lines.append(
            f"  Wooden spoon: {prizes.wooden_spoon.player_name} "
            f"({_pts(prizes.wooden_spoon.total_points)})"
        )

    return "\n".join(lines).rstrip() + "\n"
