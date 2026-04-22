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
  * Most firsts — most 1st-place finishes across the week. Tied-for-1st
    counts once for **each** tied player (matches how the 5-pt tie
    points already work).
  * Best average — highest points-per-submission. Requires at least
    ``MIN_SUBMISSIONS_FOR_AVERAGE_PRIZE`` submissions so someone can't
    win by playing one lucky round.
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

# Minimum number of submissions a player needs to be eligible for the
# "Best average" prize. Set low enough (5) that a player picking up one
# game-type a day across a work week qualifies, but high enough that one
# lucky round can't steal the prize.
MIN_SUBMISSIONS_FOR_AVERAGE_PRIZE = 5


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
    # Added alongside the "Most firsts" + "Best average" prizes. Default
    # to 0 so older tests that construct PlayerWeeklyStats positionally
    # with 5 fields keep working.
    first_places: int = 0
    submissions: int = 0

    @property
    def average_points(self) -> float:
        """Mean points per submission. Zero if the player submitted nothing."""
        if self.submissions == 0:
            return 0.0
        return self.total_points / self.submissions


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
    most_firsts: Optional[PlayerWeeklyStats]
    best_average: Optional[PlayerWeeklyStats]
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

    Groups by ``(game, puzzle_no)``, applies :func:`assign_daily_points`
    to each group, and accumulates per-player ``total_points``,
    ``distinct_games``, ``days_played``, ``submissions``, and
    ``first_places``. A player tied for 1st with someone else counts as
    1st for both — same convention as the 5-pt tie points. Returns
    sorted by ``total_points`` desc.
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
    submissions: Dict[int, int] = {}
    first_places: Dict[int, int] = {}

    for s in scores:
        player_names[s.player_id] = s.player_name
        games_by_player.setdefault(s.player_id, set()).add(s.game)
        days_by_player.setdefault(s.player_id, set()).add(s.puzzle_date)
        totals.setdefault(s.player_id, 0)
        submissions[s.player_id] = submissions.get(s.player_id, 0) + 1
        first_places.setdefault(s.player_id, 0)

    for group_scores in groups.values():
        for pid, pts in assign_daily_points(group_scores).items():
            totals[pid] += pts
        # Count every player tied at the minimum raw_score as a 1st.
        best_raw = min(s.raw_score for s in group_scores)
        for s in group_scores:
            if s.raw_score == best_raw:
                first_places[s.player_id] += 1

    leaderboard = [
        PlayerWeeklyStats(
            player_id=pid,
            player_name=player_names[pid],
            total_points=totals[pid],
            distinct_games=len(games_by_player[pid]),
            days_played=len(days_by_player[pid]),
            first_places=first_places[pid],
            submissions=submissions[pid],
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
    """Allocate the five weekly prizes from a precomputed leaderboard.

    Tiebreaks documented inline; all resolve to smallest ``player_id``.
    ``best_average`` returns ``None`` when nobody meets the submissions
    threshold. All prizes return ``None`` if the leaderboard is empty.
    """
    if not leaderboard:
        return Prizes(None, None, None, None, None)

    champion = max(
        leaderboard,
        key=lambda p: (p.total_points, -p.player_id),
    )
    all_rounder = max(
        leaderboard,
        key=lambda p: (p.distinct_games, p.total_points, -p.player_id),
    )
    most_firsts = max(
        leaderboard,
        key=lambda p: (p.first_places, p.total_points, -p.player_id),
    )

    # Best average: filter to players with enough submissions to judge
    # consistency, then pick the highest mean points per submission.
    # Tiebreak on total_points so a player with the same avg but more
    # games played wins over a coaster.
    eligible = [
        p for p in leaderboard
        if p.submissions >= MIN_SUBMISSIONS_FOR_AVERAGE_PRIZE
    ]
    best_average: Optional[PlayerWeeklyStats] = None
    if eligible:
        best_average = max(
            eligible,
            key=lambda p: (p.average_points, p.total_points, -p.player_id),
        )

    wooden_spoon = min(
        leaderboard,
        key=lambda p: (p.total_points, p.player_id),
    )
    return Prizes(
        champion=champion,
        all_rounder=all_rounder,
        most_firsts=most_firsts,
        best_average=best_average,
        wooden_spoon=wooden_spoon,
    )
