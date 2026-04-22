"""Recap/wrap text builders.

:func:`daily_recap` and :func:`weekly_wrap` are pure functions that take a
batch of :class:`~app.db.ScoreRow` records and return a WhatsApp-friendly
formatted string. Both accept an ``enabled_games`` set — scores for
disabled games are silently excluded from the output and scoring.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, FrozenSet, List, Sequence, Set, Tuple

from .db import ScoreRow
from .scoring import (
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

_GAME_ORDER: Tuple[str, ...] = (
    "queens",
    "tango",
    "crossclimb",
    "zip",
    "pinpoint",
    "patches",
    "mini_sudoku",
)

_ALL_GAMES = frozenset(_GAME_DISPLAY)


def _pts(points: int) -> str:
    return "1 pt" if points == 1 else f"{points} pts"


def _format_raw_score(game: str, raw: int) -> str:
    if game == "pinpoint":
        noun = "guess" if raw == 1 else "guesses"
        return f"{raw} {noun}"
    minutes, seconds = divmod(raw, 60)
    return f"{minutes}:{seconds:02d}"


def _games_word(n: int) -> str:
    return "1 game" if n == 1 else f"{n} games"


# ---------------------------------------------------------------------------
# daily_recap
# ---------------------------------------------------------------------------


def daily_recap(
    day: date,
    scores: Sequence[ScoreRow],
    enabled_games: FrozenSet[str] = _ALL_GAMES,
) -> str:
    """Format a daily recap string for a single ``day``."""
    header = f"Daily recap — {day.strftime('%a %d %b %Y')}"
    day_scores = [
        s for s in scores
        if s.puzzle_date == day and s.game in enabled_games
    ]

    if not day_scores:
        return f"{header}\n\nNo scores yet.\n"

    groups: Dict[Tuple[str, int], List[ScoreRow]] = {}
    for s in day_scores:
        groups.setdefault((s.game, s.puzzle_no), []).append(s)

    lines: List[str] = [header, ""]
    day_totals: Dict[int, int] = {}
    player_names: Dict[int, str] = {}
    player_game_count: Dict[int, Set[str]] = {}

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
                player_game_count.setdefault(s.player_id, set()).add(game)
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
        gc = len(player_game_count.get(pid, set()))
        lines.append(
            f"  {player_names[pid]}: {_pts(pts)} ({_games_word(gc)})"
        )

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# weekly_wrap
# ---------------------------------------------------------------------------


def weekly_wrap(
    week_start: date,
    week_end: date,
    scores: Sequence[ScoreRow],
    enabled_games: FrozenSet[str] = _ALL_GAMES,
) -> str:
    """Format a weekly wrap covering ``[week_start, week_end]`` inclusive.

    Shape: compact leaderboard (one line per player, sorted by points)
    followed by three prizes — Most firsts, Most lasts, Best average.
    Champion and Wooden spoon are implicit in the leaderboard (top and
    bottom rows), so we don't duplicate them as prizes. All-rounder
    (most distinct games) is visible in the ``(N games)`` suffix on
    each leaderboard line.

    Kept intentionally short so the whole message fits on one phone
    screen and is easy to forward from the bot's 1:1 DM into the
    friends' group chat.
    """
    header = (
        f"Weekly wrap — "
        f"{week_start.strftime('%a %d %b')} to "
        f"{week_end.strftime('%a %d %b %Y')}"
    )
    week_scores = [
        s for s in scores
        if week_start <= s.puzzle_date <= week_end
        and s.game in enabled_games
    ]

    if not week_scores:
        return f"{header}\n\nNo scores this week.\n"

    lb = weekly_leaderboard(week_scores)
    prizes = prize_allocations(lb)

    lines: List[str] = [header, ""]

    for i, p in enumerate(lb, start=1):
        lines.append(
            f"{i}. {p.player_name}: {_pts(p.total_points)} "
            f"({_games_word(p.distinct_games)})"
        )

    lines.append("")

    if prizes.most_firsts is not None:
        firsts = prizes.most_firsts.first_places
        firsts_word = "1 first" if firsts == 1 else f"{firsts} firsts"
        lines.append(
            f"Most firsts: {prizes.most_firsts.player_name} ({firsts_word})"
        )
    if prizes.most_lasts is not None:
        lasts = prizes.most_lasts.last_places
        lasts_word = "1 last" if lasts == 1 else f"{lasts} lasts"
        lines.append(
            f"Most lasts: {prizes.most_lasts.player_name} ({lasts_word})"
        )
    if prizes.best_average is not None:
        ba = prizes.best_average
        lines.append(
            f"Best average: {ba.player_name} "
            f"(avg {ba.average_points:.1f} pts/game, {ba.submissions} submissions)"
        )

    return "\n".join(lines).rstrip() + "\n"
