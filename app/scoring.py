"""Scoring logic for the LinkedIn Games tracker.

Pure functions that operate on :class:`~app.db.ScoreRow` inputs and produce
derived stats. No I/O; feed them data from the repository layer.

Rules:

- Per game per day: 1st=5, 2nd=4, 3rd=3, 4th=2, 5th=1, 6th+=0.
- Ties: average the position points the tied players would fill, then
  round **up** (``math.ceil``). E.g. tied 2nd/3rd → ceil((4+3)/2) = 4.
- Weekly total = sum of daily points across all **enabled** games.
- Per-game leader = player with the most total points in that game over
  the period.
- Prizes:

  * Champion — most total points
  * All-rounder — most distinct games played (tiebreak: points)
  * Streak king — most days played (tiebreak: points)
  * Wooden spoon — fewest total points among participants

All 7 games are "lower is better" (seconds or guess count). Daily
grouping uses ``(game, puzzle_no)`` rather than ``puzzle_date``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .db import ScoreRow

# Position → base points. Positions beyond 5 get 0.
_POSITION_POINTS: Dict[int, int] = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1}


def _tied_points(rank: int, count: int) -> int:
    """Points for ``count`` players tied starting at ``rank``.

    Average the position-points they'd fill, then ``math.ceil``.

    >>> _tied_points(2, 2)   # tied 2nd/3rd: (4+3)/2 = 3.5 → 4
    4
    >>> _tied_points(1, 3)   # tied 1st/2nd/3rd: (5+4+3)/3 = 4.0 → 4
    4
    """
    total = sum(_POSITION_POINTS.get(rank + i, 0) for i in range(count))
    return math.ceil(total / count)


@dataclass(frozen=True)
class PlayerWeeklyStats:
    player_id: int
    player_name: str
    total_points: int
    distinct_games: int
    days_played: int


@dataclass(frozen=True)
class GameLeader:
    game: str
    player_id: int
    player_name: str
    total_points: int


@dataclass(frozen=True)
class Prizes:
    champion: Optional[PlayerWeeklyStats]
    all_rounder: Optional[PlayerWeeklyStats]
    streak_king: Optional[PlayerWeeklyStats]
    wooden_spoon: Optional[PlayerWeeklyStats]


# ---------------------------------------------------------------------------
# assign_daily_points
# ---------------------------------------------------------------------------


def assign_daily_points(scores: Sequence[ScoreRow]) -> Dict[int, int]:
    """Return ``{player_id: points}`` for one daily puzzle round.

    ``scores`` should all be for a single ``(game, puzzle_no)``.
    Standard competition ranking with averaged + ceil'd tie points.
    """
    if not scores:
        return {}
    sorted_scores = sorted(scores, key=lambda s: s.raw_score)
    result: Dict[int, int] = {}
    rank = 1
    i = 0
    n = len(sorted_scores)
    while i < n:
        j = i + 1
        while j < n and sorted_scores[j].raw_score == sorted_scores[i].raw_score:
            j += 1
        tie_count = j - i
        points = _tied_points(rank, tie_count)
        for k in range(i, j):
            result[sorted_scores[k].player_id] = points
        rank += tie_count
        i = j
    return result


# ---------------------------------------------------------------------------
# weekly_leaderboard
# ---------------------------------------------------------------------------


def weekly_leaderboard(
    scores: Sequence[ScoreRow],
) -> List[PlayerWeeklyStats]:
    """Aggregate a batch of scores (one week's worth) into per-player stats.

    Groups by ``(game, puzzle_no)``, applies :func:`assign_daily_points` to
    each group, and accumulates ``total_points`` + ``distinct_games`` +
    ``days_played`` per player. Returns sorted by ``total_points`` desc.
    """
    if not scores:
        return []

    groups: Dict[Tuple[str, int], List[ScoreRow]] = {}
    for s in scores:
        groups.setdefault((s.game, s.puzzle_no), []).append(s)

    player_names: Dict[int, str] = {}
    totals: Dict[int, int] = {}
    games_by_player: Dict[int, set] = {}
    days_by_player: Dict[int, set] = {}

    for s in scores:
        player_names[s.player_id] = s.player_name
        games_by_player.setdefault(s.player_id, set()).add(s.game)
        days_by_player.setdefault(s.player_id, set()).add(s.puzzle_date)
        totals.setdefault(s.player_id, 0)

    for group_scores in groups.values():
        for pid, pts in assign_daily_points(group_scores).items():
            totals[pid] += pts

    leaderboard = [
        PlayerWeeklyStats(
            player_id=pid,
            player_name=player_names[pid],
            total_points=totals[pid],
            distinct_games=len(games_by_player[pid]),
            days_played=len(days_by_player[pid]),
        )
        for pid in player_names
    ]
    leaderboard.sort(key=lambda p: (-p.total_points, p.player_id))
    return leaderboard


# ---------------------------------------------------------------------------
# game_leaders
# ---------------------------------------------------------------------------


def game_leaders(scores: Sequence[ScoreRow]) -> List[GameLeader]:
    """For each game present in ``scores``, find the player with the most
    total points in that game over the period.

    Returns one :class:`GameLeader` per game, sorted by
    ``_GAME_ORDER``-ish (actually by first appearance in scores).
    """
    groups: Dict[Tuple[str, int], List[ScoreRow]] = {}
    for s in scores:
        groups.setdefault((s.game, s.puzzle_no), []).append(s)

    # Per-player per-game point totals
    game_player_pts: Dict[str, Dict[int, int]] = {}
    player_names: Dict[int, str] = {}

    for (game, _), group_scores in groups.items():
        pts_map = assign_daily_points(group_scores)
        bucket = game_player_pts.setdefault(game, {})
        for pid, pts in pts_map.items():
            bucket[pid] = bucket.get(pid, 0) + pts

    for s in scores:
        player_names[s.player_id] = s.player_name

    result: List[GameLeader] = []
    for game, player_pts in game_player_pts.items():
        best_pid = max(
            player_pts,
            key=lambda p: (player_pts[p], -p),
        )
        result.append(
            GameLeader(
                game=game,
                player_id=best_pid,
                player_name=player_names.get(best_pid, ""),
                total_points=player_pts[best_pid],
            )
        )
    return result


# ---------------------------------------------------------------------------
# prize_allocations
# ---------------------------------------------------------------------------


def prize_allocations(leaderboard: Sequence[PlayerWeeklyStats]) -> Prizes:
    """Allocate the four weekly prizes from a precomputed leaderboard.

    Tiebreaks documented inline; all resolve to smallest ``player_id``.
    Returns all ``None`` if the leaderboard is empty.
    """
    if not leaderboard:
        return Prizes(None, None, None, None)

    champion = max(
        leaderboard,
        key=lambda p: (p.total_points, -p.player_id),
    )
    all_rounder = max(
        leaderboard,
        key=lambda p: (p.distinct_games, p.total_points, -p.player_id),
    )
    streak_king = max(
        leaderboard,
        key=lambda p: (p.days_played, p.total_points, -p.player_id),
    )
    wooden_spoon = min(
        leaderboard,
        key=lambda p: (p.total_points, p.player_id),
    )
    return Prizes(
        champion=champion,
        all_rounder=all_rounder,
        streak_king=streak_king,
        wooden_spoon=wooden_spoon,
    )
