"""Historical derived statistics requiring repo I/O.

Kept separate from :mod:`app.scoring` (which is pure) so the I/O
boundary stays clean.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import FrozenSet

from .db import Repository
from .puzzles import week_bounds
from .scoring import weekly_leaderboard


def podium_streak(
    repo: Repository,
    *,
    player_id: int,
    group_id: int,
    reference_week_monday: date,
    enabled_games: FrozenSet[str],
    max_weeks: int = 52,
) -> int:
    """Return consecutive completed weeks (going back from the week before
    ``reference_week_monday``) in which ``player_id`` finished top 3.

    Stops at the first non-podium week or first week with no data for
    the player. Returns 0 if not currently on a streak.
    """
    streak = 0
    check_monday = reference_week_monday - timedelta(weeks=1)
    for _ in range(max_weeks):
        sun = check_monday + timedelta(days=6)
        try:
            week_scores = repo.list_scores(
                date_from=check_monday, date_to=sun, group_id=group_id
            )
        except Exception:
            break
        filtered = [s for s in week_scores if s.game in enabled_games]
        lb = weekly_leaderboard(filtered)
        rank = next(
            (i for i, e in enumerate(lb, 1) if e.player_id == player_id), None
        )
        if rank is None or rank > 3:
            break
        streak += 1
        check_monday -= timedelta(weeks=1)
    return streak
