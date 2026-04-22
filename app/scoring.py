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
- Prizes (three; Champion / All-rounder / Wooden spoon were dropped
  because the overall leaderboard already shows #1 and last, and
  distinct-games is already visible as "(N games)" on each line):

  * Most firsts — most 1st-place finishes in *competitive* rounds
    (≥2 players). Tied-for-1st counts once for **each** tied player.
  * Most lasts — the flip side: most last-place finishes in competitive
    rounds. Singletons don't count (being alone isn't "losing").
  * Best average — highest points-per-submission. Requires at least
    ``MIN_SUBMISSIONS_FOR_AVERAGE_PRIZE`` submissions so someone can't
    win by playing one lucky round.

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
    # Added alongside the Most firsts / Most lasts / Best average prizes.
    # Defaults let older tests that constructed PlayerWeeklyStats with
    # just 5 positional fields keep working. first_places and
    # last_places are *competitive* counts: singleton rounds (where only
    # one player submitted that game) don't contribute to either.
    first_places: int = 0
    last_places: int = 0
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
    most_firsts: Optional[PlayerWeeklyStats]
    most_lasts: Optional[PlayerWeeklyStats]
    best_average: Optional[PlayerWeeklyStats]


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

    last_places: Dict[int, int] = {pid: 0 for pid in first_places}

    for group_scores in groups.values():
        for pid, pts in assign_daily_points(group_scores).items():
            totals[pid] += pts
        # First / last only count when there's real competition — a lone
        # submitter would otherwise sweep both metrics absurdly.
        if len(group_scores) < 2:
            continue
        best_raw = min(s.raw_score for s in group_scores)
        worst_raw = max(s.raw_score for s in group_scores)
        for s in group_scores:
            if s.raw_score == best_raw:
                first_places[s.player_id] += 1
            if s.raw_score == worst_raw:
                last_places[s.player_id] += 1

    leaderboard = [
        PlayerWeeklyStats(
            player_id=pid,
            player_name=player_names[pid],
            total_points=totals[pid],
            distinct_games=len(games_by_player[pid]),
            days_played=len(days_by_player[pid]),
            first_places=first_places[pid],
            last_places=last_places[pid],
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

    Returns one :class:`GameLeader` per game, ordered by first
    appearance in ``scores``. Callers that want a canonical display
    order should reorder against :data:`app.parsers.GAME_DISPLAY_ORDER`.
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
    """Allocate the three weekly prizes from a precomputed leaderboard.

    Tiebreaks documented inline; all resolve to smallest ``player_id``.
    ``most_firsts`` / ``most_lasts`` return ``None`` when nobody played
    any competitive rounds (≥2 players) that week. ``best_average``
    returns ``None`` when nobody meets the submissions threshold. All
    three return ``None`` if the leaderboard is empty.
    """
    if not leaderboard:
        return Prizes(None, None, None)

    most_firsts: Optional[PlayerWeeklyStats] = None
    has_any_first = any(p.first_places > 0 for p in leaderboard)
    if has_any_first:
        most_firsts = max(
            leaderboard,
            key=lambda p: (p.first_places, p.total_points, -p.player_id),
        )

    most_lasts: Optional[PlayerWeeklyStats] = None
    has_any_last = any(p.last_places > 0 for p in leaderboard)
    if has_any_last:
        most_lasts = max(
            leaderboard,
            key=lambda p: (p.last_places, -p.total_points, -p.player_id),
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

    return Prizes(
        most_firsts=most_firsts,
        most_lasts=most_lasts,
        best_average=best_average,
    )
