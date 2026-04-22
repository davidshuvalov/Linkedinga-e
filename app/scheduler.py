"""Recap/wrap text builders.

:func:`daily_recap` and :func:`weekly_wrap` are pure functions that take a
batch of :class:`~app.db.ScoreRow` records and return a WhatsApp-friendly
formatted string — one message per call, kept short enough to copy-paste
or forward from a 1:1 DM into the friends' group chat.

Both accept an ``enabled_games`` set; scores for disabled games are
silently excluded from the output and from the scoring totals.

Both also accept the **whole week's scores**, not just one day's, so
the daily message can show a running "Week so far" leaderboard and the
weekly wrap can reconcile the final standings.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, FrozenSet, List, Sequence, Tuple

from .db import ScoreRow
from .parsers import GAME_DISPLAY, GAME_DISPLAY_ORDER, format_raw_score
from .scoring import (
    assign_daily_points,
    game_leaders,
    prize_allocations,
    weekly_leaderboard,
)

_ALL_GAMES = frozenset(GAME_DISPLAY)


def _pts(points: int) -> str:
    return "1 pt" if points == 1 else f"{points} pts"


def _games_word(n: int) -> str:
    return "1 game" if n == 1 else f"{n} games"


# ---------------------------------------------------------------------------
# Shared section builders
# ---------------------------------------------------------------------------


def _per_game_sections(
    day: date,
    day_scores: Sequence[ScoreRow],
) -> List[str]:
    """Build the per-game rankings block for ``day``.

    Returns a list of lines; caller decides how to join with surrounding
    content. Games with no submissions for the day are silently skipped.
    Groups are ordered by ``GAME_DISPLAY_ORDER`` and then by puzzle number
    (usually one puzzle per game per day, but this is future-proof).
    """
    groups: Dict[Tuple[str, int], List[ScoreRow]] = {}
    for s in day_scores:
        groups.setdefault((s.game, s.puzzle_no), []).append(s)

    lines: List[str] = []
    for game in GAME_DISPLAY_ORDER:
        matching = sorted(
            (key for key in groups if key[0] == game),
            key=lambda k: k[1],
        )
        for key in matching:
            group_scores = sorted(groups[key], key=lambda s: s.raw_score)
            points_map = assign_daily_points(group_scores)
            lines.append(f"{GAME_DISPLAY[game]} #{key[1]}")
            for s in group_scores:
                pts = points_map[s.player_id]
                lines.append(
                    f"  {s.player_name} — "
                    f"{format_raw_score(game, s.raw_score)} ({_pts(pts)})"
                )
            lines.append("")
    # Drop the trailing blank so the caller controls spacing.
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _weekly_leaderboard_lines(
    week_scores: Sequence[ScoreRow],
    title: str = "Week so far",
) -> List[str]:
    """Render the cumulative weekly leaderboard as a compact list.

    Used by both the daily recap (midweek: "Week so far") and the
    weekly wrap (final: "Week totals"). Returns empty list if nobody
    has submitted anything this week.
    """
    lb = weekly_leaderboard(week_scores)
    if not lb:
        return []
    lines: List[str] = [f"{title}:"]
    for i, p in enumerate(lb, start=1):
        lines.append(
            f"  {i}. {p.player_name}: {_pts(p.total_points)} "
            f"({_games_word(p.distinct_games)})"
        )
    return lines


# ---------------------------------------------------------------------------
# daily_recap
# ---------------------------------------------------------------------------


def daily_recap(
    day: date,
    week_scores: Sequence[ScoreRow],
    enabled_games: FrozenSet[str] = _ALL_GAMES,
) -> str:
    """Format a daily recap for ``day``.

    Contents:
    - Per-game rankings for every round played that day.
    - Running "Week so far" cumulative leaderboard across the whole week
      (which is why ``week_scores`` is the **whole week**, not just
      ``day``'s slice).

    ``week_scores`` must include ``day``'s scores. Scores for disabled
    games are filtered out before rendering and before leaderboard
    aggregation.
    """
    header = f"Daily recap — {day.strftime('%a %d %b %Y')}"

    week_filtered = [s for s in week_scores if s.game in enabled_games]
    day_scores = [s for s in week_filtered if s.puzzle_date == day]

    if not day_scores:
        return f"{header}\n\nNo scores yet.\n"

    lines: List[str] = [header, ""]
    lines.extend(_per_game_sections(day, day_scores))

    lb_lines = _weekly_leaderboard_lines(week_filtered, title="Week so far")
    if lb_lines:
        lines.append("")
        lines.extend(lb_lines)

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# weekly_wrap
# ---------------------------------------------------------------------------


def weekly_wrap(
    week_start: date,
    week_end: date,
    week_scores: Sequence[ScoreRow],
    enabled_games: FrozenSet[str] = _ALL_GAMES,
) -> str:
    """Format a weekly wrap covering ``[week_start, week_end]`` inclusive.

    Contents (all in one message):

    - Per-game rankings for ``week_end`` (the final day — usually
      Sunday LA — so Sunday's scores still get their own spotlight).
    - Final "Week totals" leaderboard (same shape as the daily
      "Week so far" block, but named differently to signal closure).
    - Per-game **weekly** winners — who accumulated the most points
      in each game across the whole week.
    - Three prizes: Most firsts, Most lasts, Best average.

    Kept under ~40 lines so the whole message copies into one forward.
    """
    header = (
        f"Weekly wrap — "
        f"{week_start.strftime('%a %d %b')} to "
        f"{week_end.strftime('%a %d %b %Y')}"
    )
    week_filtered = [
        s for s in week_scores
        if week_start <= s.puzzle_date <= week_end
        and s.game in enabled_games
    ]

    if not week_filtered:
        return f"{header}\n\nNo scores this week.\n"

    lines: List[str] = [header, ""]

    # Final day's per-game rankings
    final_day_scores = [s for s in week_filtered if s.puzzle_date == week_end]
    if final_day_scores:
        lines.append(f"{week_end.strftime('%a %d %b')}:")
        lines.append("")
        lines.extend(_per_game_sections(week_end, final_day_scores))
        lines.append("")

    # Week totals leaderboard
    lines.extend(_weekly_leaderboard_lines(week_filtered, title="Week totals"))

    # Per-game weekly winners
    leaders = game_leaders(week_filtered)
    if leaders:
        lines.append("")
        lines.append("Game winners:")
        for game in GAME_DISPLAY_ORDER:
            gl = next((g for g in leaders if g.game == game), None)
            if gl is not None:
                lines.append(
                    f"  {GAME_DISPLAY[game]}: "
                    f"{gl.player_name} ({_pts(gl.total_points)})"
                )

    # Prizes
    lb = weekly_leaderboard(week_filtered)
    prizes = prize_allocations(lb)
    prize_lines: List[str] = []
    if prizes.most_firsts is not None:
        firsts = prizes.most_firsts.first_places
        firsts_word = "1 first" if firsts == 1 else f"{firsts} firsts"
        prize_lines.append(
            f"  Most firsts: {prizes.most_firsts.player_name} ({firsts_word})"
        )
    if prizes.most_lasts is not None:
        lasts = prizes.most_lasts.last_places
        lasts_word = "1 last" if lasts == 1 else f"{lasts} lasts"
        prize_lines.append(
            f"  Most lasts: {prizes.most_lasts.player_name} ({lasts_word})"
        )
    if prizes.best_average is not None:
        ba = prizes.best_average
        prize_lines.append(
            f"  Best average: {ba.player_name} "
            f"(avg {ba.average_points:.1f} pts/game, {ba.submissions} submissions)"
        )
    if prize_lines:
        lines.append("")
        lines.append("Prizes:")
        lines.extend(prize_lines)

    return "\n".join(lines).rstrip() + "\n"
