"""Scoring logic for the LinkedIn Games tracker.

Pure functions that operate on :class:`~app.db.ScoreRow` inputs and produce
derived stats. No I/O; feed them data from the repository layer.

Rules (from the build spec):

- Rank-based per game per day: 1st=5, 2nd=3, 3rd=1, others=0.
- Ties share the better rank (standard competition ranking, 1-1-3-4).
- Weekly total = sum of daily points across all 7 games.
- Prizes:

  * Champion — most total points
  * All-rounder — most distinct games played (tiebreak: points)
  * Streak king — most days played (tiebreak: points)
  * Wooden spoon — fewest total points among participants

All 7 games are "lower is better" (seconds or guess count), which is why
the sort key below is ``raw_score`` ascending.

Daily grouping uses ``(game, puzzle_no)`` rather than ``puzzle_date`` so a
player who submits Queens #714 at 11:58 PM Monday and a player who submits
the same #714 at 12:02 AM Tuesday still share the same daily round even
though their stored ``puzzle_date`` values differ.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .db import ScoreRow

# Rank -> points. Ranks beyond 3 get 0.
_DAILY_POINTS: Dict[int, int] = {1: 5, 2: 3, 3: 1}


@dataclass(frozen=True)
class PlayerWeeklyStats:
    player_id: int
    player_name: str
    total_points: int
    distinct_games: int
    days_played: int


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

    ``scores`` should all be for a single ``(game, puzzle_no)`` — the
    function does not re-check that. Standard competition ranking is
    applied: players tied on ``raw_score`` share the better rank, and the
    next rank is skipped by the tie count.
    """
    if not scores:
        return {}
    sorted_scores = sorted(scores, key=lambda s: s.raw_score)
    result: Dict[int, int] = {}
    rank = 1
    i = 0
    n = len(sorted_scores)
    while i < n:
        # Walk the tie group at position i.
        j = i + 1
        while j < n and sorted_scores[j].raw_score == sorted_scores[i].raw_score:
            j += 1
        points = _DAILY_POINTS.get(rank, 0)
        for k in range(i, j):
            result[sorted_scores[k].player_id] = points
        rank += j - i  # skip ahead by the number of tied players
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
    ``days_played`` per player. Returns a list sorted by ``total_points``
    descending, with ties broken by ``player_id`` ascending for stability.
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
# prize_allocations
# ---------------------------------------------------------------------------


def prize_allocations(leaderboard: Sequence[PlayerWeeklyStats]) -> Prizes:
    """Allocate the four weekly prizes from a precomputed leaderboard.

    Tiebreaks:

    * Champion: highest ``total_points``; ties broken by smallest
      ``player_id``.
    * All-rounder: highest ``distinct_games``; ties broken by
      ``total_points``, then ``player_id``.
    * Streak king: highest ``days_played``; ties broken by
      ``total_points``, then ``player_id``.
    * Wooden spoon: lowest ``total_points``; ties broken by smallest
      ``player_id``.

    Returns :class:`Prizes` with all four fields set to ``None`` if the
    leaderboard is empty.
    """
    if not leaderboard:
        return Prizes(None, None, None, None)

    # For "max with ties broken by smaller player_id", negate player_id so
    # that larger tuples still win the max.
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
