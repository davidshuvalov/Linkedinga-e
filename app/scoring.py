"""Scoring logic for the LinkedIn Games tracker.

Pure functions that operate on :class:`~app.db.ScoreRow` inputs and produce
derived stats. No I/O; feed them data from the repository layer.

Rules:

- Per game per day: 1st=5, 2nd=4, 3rd=3, 4th=2, 5th=1, 6th+=0.
- Ties: tied players split the sum of the positions they'd fill. No
  rounding — tied 2nd/3rd both get (4+3)/2 = 3.5, tied 1st/2nd both
  get 4.5, three-way tie at top all get 4.0. Keeps the round total
  invariant at 15 regardless of how many are tied.
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

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .db import ScoreRow

# Position → base points. Positions beyond 5 get 0.
_POSITION_POINTS: Dict[int, int] = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1}

# Minimum number of submissions a player needs to be eligible for the
# "Best average" prize. Set low enough (5) that a player picking up one
# game-type a day across a work week qualifies, but high enough that one
# lucky round can't steal the prize.
MIN_SUBMISSIONS_FOR_AVERAGE_PRIZE = 5

# Floor for the "Fastest average time" prize. Higher than the average-points
# floor because we're now ranking on a per-round mean: a player with 10
# rounds averaging 30s shouldn't lose to one who happened to play five
# fast Queens rounds and stop. 10 keeps the prize honest across volume.
MIN_SUBMISSIONS_FOR_FASTEST_PRIZE = 10

# Pinpoint is guess count, not seconds, so it's excluded from the
# total-time prize. Every other game stores raw_score in seconds.
_NON_TIME_GAMES = frozenset({"pinpoint"})


def _tied_points(rank: int, count: int) -> float:
    """Points for ``count`` players tied starting at ``rank``.

    Tied players split the sum of the positions they fill. No rounding
    — returned as a float so the total points awarded per round stays
    invariant (5+4+3+2+1 = 15, regardless of how many are tied).

    >>> _tied_points(2, 2)   # tied 2nd/3rd: (4+3)/2 = 3.5
    3.5
    >>> _tied_points(1, 2)   # tied 1st/2nd: (5+4)/2 = 4.5
    4.5
    >>> _tied_points(1, 3)   # tied 1st/2nd/3rd: (5+4+3)/3 = 4.0
    4.0
    """
    total = sum(_POSITION_POINTS.get(rank + i, 0) for i in range(count))
    return total / count


@dataclass(frozen=True)
class PlayerWeeklyStats:
    player_id: int
    player_name: str
    # Competitive scoring emits floats (e.g. 7.0, 3.2) for 3–5 player
    # time-based rounds, so totals aggregate as floats too. Integers
    # still work unchanged for legacy / fallback rounds.
    total_points: float
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
    # Total seconds accumulated across time-based games (everything
    # except pinpoint, which is a guess count). Feeds the "Fastest
    # total time" prize. ``time_based_submissions`` is the count of
    # rounds that contributed to ``total_time`` — used for the
    # min-submissions gate so one fluky round can't steal the prize.
    total_time: int = 0
    time_based_submissions: int = 0

    @property
    def average_points(self) -> float:
        """Mean points per submission. Zero if the player submitted nothing."""
        if self.submissions == 0:
            return 0.0
        return self.total_points / self.submissions

    @property
    def average_time(self) -> float:
        """Mean seconds per time-based round. Zero if no time-based subs."""
        if self.time_based_submissions == 0:
            return 0.0
        return self.total_time / self.time_based_submissions


@dataclass(frozen=True)
class GameLeader:
    game: str
    player_id: int
    player_name: str
    total_points: float


@dataclass(frozen=True)
class Prizes:
    most_firsts: Optional[PlayerWeeklyStats]
    most_lasts: Optional[PlayerWeeklyStats]
    best_average: Optional[PlayerWeeklyStats]
    # Lowest mean seconds per time-based round for the week, gated on
    # MIN_SUBMISSIONS_FOR_FASTEST_PRIZE so a player with one fluky run
    # can't beat someone who's grinding consistent times across the week.
    fastest_average_time: Optional[PlayerWeeklyStats] = None


# ---------------------------------------------------------------------------
# assign_daily_points
# ---------------------------------------------------------------------------


def _legacy_rank_points(
    sorted_scores: Sequence[ScoreRow],
) -> Dict[int, float]:
    """Rank-based 5/4/3/2/1 with ceil'd averaged tie points.

    ``sorted_scores`` must already be sorted ascending by ``raw_score``.
    Retained as the fallback path for cases :func:`competitive_score`
    can't handle (pinpoint, ties, rounds outside 3–5 players).
    """
    result: Dict[int, float] = {}
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
            result[sorted_scores[k].player_id] = float(points)
        rank += tie_count
        i = j
    return result


def _has_ties(sorted_scores: Sequence[ScoreRow]) -> bool:
    """True when any two rows share ``raw_score``. ``competitive_score``
    breaks ties by input order (not fair), so ties route through the
    legacy helper instead."""
    raws = [s.raw_score for s in sorted_scores]
    return len(set(raws)) < len(raws)


def assign_daily_points(scores: Sequence[ScoreRow]) -> Dict[int, float]:
    """Return ``{player_id: points}`` for one daily puzzle round.

    Dispatches between two systems based on the round shape:

    - **competitive_score** (rank + time-performance blended; the
      CASE A/B/C algorithm) for any round of 3+ players in a
      time-based game. Handles ties inline by averaging base
      points and sharing any awarded bonus. Emits floats rounded
      to 1 d.p. Round total stays invariant per round size
      (12 for 3p, 14 for 4p, 15 for 5+).
    - **legacy rank-based** shares of positional points for
      pinpoint (guess count, not seconds) and 1–2 player rounds
      where the ratio model has nothing to bite on. Tied players
      split the sum of the positions they'd fill; no ceiling
      applied so the round total stays invariant.

    ``scores`` should all be for a single ``(game, puzzle_no)``.
    """
    if not scores:
        return {}
    sorted_scores = sorted(scores, key=lambda s: s.raw_score)
    n = len(sorted_scores)

    # Pinpoint is guess count (1–5); the ratio/spread model the
    # competitive algorithm uses would treat 1 vs 2 guesses as a 2x
    # "time" difference which is nonsense. Route all pinpoint rounds
    # through the legacy rank system.
    pinpoint_free = all(s.game not in _NON_TIME_GAMES for s in sorted_scores)
    big_enough = n >= 3

    if pinpoint_free and big_enough:
        players = [
            {"name": s.player_name, "time": s.raw_score}
            for s in sorted_scores
        ]
        results = competitive_score(players)
        return {
            sorted_scores[i].player_id: results[i]["final_score"]
            for i in range(n)
        }

    return _legacy_rank_points(sorted_scores)


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
    totals: Dict[int, float] = {}
    games_by_player: Dict[int, set] = {}
    days_by_player: Dict[int, set] = {}
    submissions: Dict[int, int] = {}
    first_places: Dict[int, int] = {}
    total_time: Dict[int, int] = {}
    time_based_submissions: Dict[int, int] = {}

    for s in scores:
        player_names[s.player_id] = s.player_name
        games_by_player.setdefault(s.player_id, set()).add(s.game)
        days_by_player.setdefault(s.player_id, set()).add(s.puzzle_date)
        totals.setdefault(s.player_id, 0.0)
        submissions[s.player_id] = submissions.get(s.player_id, 0) + 1
        first_places.setdefault(s.player_id, 0)
        total_time.setdefault(s.player_id, 0)
        time_based_submissions.setdefault(s.player_id, 0)
        # Only seconds-valued games count toward the total-time prize;
        # pinpoint's guess count would corrupt the sum.
        if s.game not in _NON_TIME_GAMES:
            total_time[s.player_id] += s.raw_score
            time_based_submissions[s.player_id] += 1

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
            # Round aggregated totals to 1 d.p. — summing 1-d.p.
            # floats across many rounds accumulates FP dust that
            # would otherwise surface in display as "7.0000001".
            total_points=round(totals[pid], 1),
            distinct_games=len(games_by_player[pid]),
            days_played=len(days_by_player[pid]),
            first_places=first_places[pid],
            last_places=last_places[pid],
            submissions=submissions[pid],
            total_time=total_time[pid],
            time_based_submissions=time_based_submissions[pid],
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
    game_player_pts: Dict[str, Dict[int, float]] = {}
    player_names: Dict[int, str] = {}

    for (game, _), group_scores in groups.items():
        pts_map = assign_daily_points(group_scores)
        bucket = game_player_pts.setdefault(game, {})
        for pid, pts in pts_map.items():
            bucket[pid] = bucket.get(pid, 0.0) + pts

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
                # Same FP-dust rounding guard as in weekly_leaderboard.
                total_points=round(player_pts[best_pid], 1),
            )
        )
    return result


# ---------------------------------------------------------------------------
# prize_allocations
# ---------------------------------------------------------------------------


def prize_allocations(leaderboard: Sequence[PlayerWeeklyStats]) -> Prizes:
    """Allocate the weekly prizes from a precomputed leaderboard.

    Tiebreaks documented inline; all resolve to smallest ``player_id``.
    ``most_firsts`` / ``most_lasts`` return ``None`` when nobody played
    any competitive rounds (≥2 players) that week. ``best_average`` and
    ``fastest_average_time`` return ``None`` when nobody meets the
    submissions threshold. All prizes return ``None`` if the
    leaderboard is empty.
    """
    if not leaderboard:
        return Prizes(None, None, None, None)

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

    # Fastest average time: lowest mean seconds per time-based round.
    # Gate on time_based_submissions so somebody who only played a
    # handful of fast rounds can't outrank a player grinding consistent
    # times across many. Tiebreak favours whoever played more rounds
    # (more impressive at the same average).
    fastest_eligible = [
        p for p in leaderboard
        if p.time_based_submissions >= MIN_SUBMISSIONS_FOR_FASTEST_PRIZE
    ]
    fastest_average_time: Optional[PlayerWeeklyStats] = None
    if fastest_eligible:
        fastest_average_time = min(
            fastest_eligible,
            key=lambda p: (p.average_time, -p.time_based_submissions, p.player_id),
        )

    return Prizes(
        most_firsts=most_firsts,
        most_lasts=most_lasts,
        best_average=best_average,
        fastest_average_time=fastest_average_time,
    )


# ---------------------------------------------------------------------------
# competitive_score: rank + time-performance blended scoring
# ---------------------------------------------------------------------------

# Spec thresholds. Named constants so the classification reads like the spec.
_TIGHT_SPREAD_THRESHOLD = 0.5        # Case A: spread < this
_CLUSTER_TOP_RATIO = 1.4             # All in-cluster consecutive ratios <
_CLUSTER_DROP_RATIO = 1.3            # Drop ratio after cluster > this fires
                                     # any cluster bonus at all
_CLEAR_WINNER_RATIO = 1.3            # Case C: r12 > this  (lowered from
                                     # 1.5 so a clear-but-not-runaway
                                     # leader — Mini-Sudoku-shaped 1.35x
                                     # gaps — still earns a bonus instead
                                     # of falling through to flat ranks)
_CLEAR_WINNER_MAX_BONUS = 2.0        # hard cap in Case C
_MIN_SCORE = 0.0                     # floor for any player — only
                                     # prevents negatives from debits;
                                     # positions 6+ naturally stay at 0
                                     # unless they tie with 5th place

# Cluster bonus pool scales piecewise-linearly with the drop ratio
# (the gap between the last in-cluster player and the first
# straggler). The shape was tuned to give modest drops a token
# bonus and runaway blowouts the full pool:
#
#   drop ≤ 1.3  → no cluster fires (handled by _detect_cluster)
#   drop = 1.3  → pool 0.5  (smallest meaningful bonus)
#   drop = 1.6  → pool 1.5  (matches the historical fixed pool)
#   drop ≥ 2.0  → pool 2.0  (cap; runaway blowouts can't grow further)
#
# Same formula applies to top-2, top-3, and top-4 clusters; with
# more winners each one's per-player share is smaller, but the
# punch on the stragglers stays meaningful because the debit per
# straggler is pool / (n - k).
_CLUSTER_POOL_DROP_MIN = 1.3
_CLUSTER_POOL_DROP_KNEE = 1.6
_CLUSTER_POOL_DROP_MAX = 2.0
_CLUSTER_POOL_AT_MIN = 0.5
_CLUSTER_POOL_AT_KNEE = 1.5
_CLUSTER_POOL_AT_MAX = 2.0


def _scaled_cluster_pool(drop_ratio: float) -> float:
    """Map the post-cluster drop ratio to its bonus pool size.

    Piecewise-linear: 0.5 pts at drop=1.3, 1.5 pts at drop=1.6,
    2.0 pts at drop=2.0+. Caller must already have confirmed the
    drop is past :data:`_CLUSTER_DROP_RATIO`.
    """
    if drop_ratio >= _CLUSTER_POOL_DROP_MAX:
        return _CLUSTER_POOL_AT_MAX
    if drop_ratio >= _CLUSTER_POOL_DROP_KNEE:
        # Knee → max segment.
        span = _CLUSTER_POOL_DROP_MAX - _CLUSTER_POOL_DROP_KNEE
        rise = _CLUSTER_POOL_AT_MAX - _CLUSTER_POOL_AT_KNEE
        return _CLUSTER_POOL_AT_KNEE + (drop_ratio - _CLUSTER_POOL_DROP_KNEE) * (rise / span)
    # Min → knee segment.
    span = _CLUSTER_POOL_DROP_KNEE - _CLUSTER_POOL_DROP_MIN
    rise = _CLUSTER_POOL_AT_KNEE - _CLUSTER_POOL_AT_MIN
    return _CLUSTER_POOL_AT_MIN + (drop_ratio - _CLUSTER_POOL_DROP_MIN) * (rise / span)


def _base_points_for_size(n: int) -> List[int]:
    """Base points by rank for an ``n``-player round.

    Reuses :data:`_POSITION_POINTS` (1st=5, 2nd=4, … 5th=1, 6th+=0)
    so a 5-player round gets ``[5,4,3,2,1]`` (total 15) and a
    6-player round gets ``[5,4,3,2,1,0]`` (total still 15). The
    spec only enumerates 3/4/5 explicitly, but the natural
    extension keeps the round total invariant at 15 for any
    ``n >= 5`` and at 12/14 for 3/4-player rounds — matching the
    legacy convention the bot already uses for 6+ player rounds.
    """
    return [_POSITION_POINTS.get(rank, 0) for rank in range(1, n + 1)]


def _detect_cluster(
    ratios: Sequence[float], n: int
) -> Optional[Tuple[int, float]]:
    """Return ``(cluster_size, drop_ratio)`` when the round has a
    "top-k tight pack with a clear drop to position k+1" shape, else
    ``None``.

    ``ratios[i]`` is ``times[i+1] / times[i]`` (so ``ratios[0]`` is
    r12). We probe the largest cluster first so a top-4 round doesn't
    silently match the top-3 rule and leave the 4th-placed player out
    of the bonus pack.

    A cluster of size ``k`` requires:

    * All ``k - 1`` in-cluster ratios are tight (``< 1.4``)
    * The ``k``-to-``k+1`` ratio is a clear drop (``> 1.3``)
    * The round has at least one player past the cluster (so the
      drop position exists)

    The drop ratio is returned alongside the size so the caller can
    look up the scaled bonus pool — bigger drops earn bigger pools.
    Returns ``None`` for any round that doesn't fit.
    """
    for k in (4, 3, 2):
        if n <= k:
            # Need at least one player past the cluster for the drop
            # check to be meaningful.
            continue
        in_cluster_tight = all(
            ratios[i] < _CLUSTER_TOP_RATIO for i in range(k - 1)
        )
        drop_ratio = ratios[k - 1]
        if in_cluster_tight and drop_ratio > _CLUSTER_DROP_RATIO:
            return k, drop_ratio
    return None


def _classify_round(
    n: int, spread: float, ratios: Sequence[float]
) -> Tuple[str, Optional[int], Optional[float]]:
    """Decide which adjustment regime applies.

    Returns ``(regime, cluster_size, drop_ratio)`` where ``regime``
    is one of ``"tight"`` / ``"cluster"`` / ``"winner"``. The
    ``cluster_size`` and ``drop_ratio`` are populated only when
    ``regime == "cluster"``; otherwise both are ``None``.

    Order of precedence (largest cluster wins, then clear-winner,
    then tight). Tight short-circuits on a small overall spread —
    the spec's "only adjust in clear cases" rule.
    """
    if spread < _TIGHT_SPREAD_THRESHOLD:
        return "tight", None, None

    detected = _detect_cluster(ratios, n)
    if detected is not None:
        cluster_size, drop_ratio = detected
        return "cluster", cluster_size, drop_ratio

    if ratios[0] > _CLEAR_WINNER_RATIO:
        return "winner", None, None

    return "tight", None, None


def _apply_cluster_bonus(
    scores: List[float],
    times: Sequence[float],
    n: int,
    top_k: int,
    pool: float,
) -> None:
    """Boost the front ``top_k`` cluster, debit the stragglers.

    Mutates ``scores`` in place. In-cluster bonuses are time-weighted
    (``1/time_i``, normalised) so the fastest of the cluster gets the
    biggest share. The full bonus pool is then subtracted evenly from
    the players past the cluster, keeping the running total equal to
    the base. ``pool`` is computed by :func:`_scaled_cluster_pool`
    from the post-cluster drop ratio — modest drops earn small pools,
    runaway blowouts earn the full 2.0.
    """
    weights = [1.0 / times[i] for i in range(top_k)]
    wsum = sum(weights)
    for i in range(top_k):
        scores[i] += pool * weights[i] / wsum

    bottom_count = n - top_k
    if bottom_count > 0:
        per_player = pool / bottom_count
        for i in range(top_k, n):
            scores[i] -= per_player


def _apply_clear_winner_bonus(
    scores: List[float],
    base_points: Sequence[float],
    times: Sequence[float],
    r12: float,
    n: int,
) -> None:
    """Case C — big gap to 1st, so top-heavy the points.

    Bonus grows with the first-to-second ratio but is capped at +2 so a
    runaway winner (r12 = 10x) doesn't blow the scale. The bonus is
    then subtracted from the other players proportionally to their
    base points — stronger mid-pack finishers absorb more of the hit.

    If multiple players are tied for 1st (same ``times[0]``) the
    bonus is shared equally — honours the "if they are far ahead
    then they share the bonus" rule. Note: in practice tied 1st
    makes ``r12 = 1.0`` which keeps Case C from triggering in the
    first place, so this branch is defensive.
    """
    bonus = min(_CLEAR_WINNER_MAX_BONUS, (r12 - 1.0) * 2.0)

    tied_with_1st = 1
    while tied_with_1st < n and times[tied_with_1st] == times[0]:
        tied_with_1st += 1

    for i in range(tied_with_1st):
        scores[i] += bonus / tied_with_1st

    other_base_sum = sum(base_points[tied_with_1st:])
    if other_base_sum > 0:
        for i in range(tied_with_1st, n):
            scores[i] -= bonus * base_points[i] / other_base_sum


def _floor_and_rebalance(
    scores: List[float], total_base: int, n: int
) -> List[float]:
    """Enforce the 0 floor then rebalance the sum back to ``total_base``.

    Only the cluster debit (Case B) can push a base-0 position below
    zero; the floor exists purely to preserve the "no negative
    scores" hard constraint, not to bump every low-ranked player up
    to a participation minimum. Positions 6+ naturally stay at 0
    unless they tie with 5th place (in which case base-points
    averaging gives the tied pair 0.5 each via ``(1+0)/2``).

    Flooring a negative score to 0 adds that deficit back to the
    total; a single ``scale_factor`` multiply restores the invariant
    without reordering rankings.
    """
    for i in range(n):
        if scores[i] < _MIN_SCORE:
            scores[i] = _MIN_SCORE

    current_total = sum(scores)
    if current_total > 0 and abs(current_total - total_base) > 1e-9:
        scale_factor = total_base / current_total
        scores = [s * scale_factor for s in scores]

    for i in range(n):
        if scores[i] < _MIN_SCORE:
            scores[i] = _MIN_SCORE

    return scores


def _round_and_reconcile(
    scores: Sequence[float], total_base: int, n: int
) -> List[float]:
    """Round each score to 1 d.p. and absorb the residue on the last row.

    Rounding independently drifts the sum off ``total_base`` by up to
    ``n * 0.05``; the spec resolves that by dumping the difference on
    the slowest player. We clamp that back to the 0 floor if the
    residue would drive them negative — accepts a ~0.1-pt total
    mismatch in that rare case rather than violate the
    "no negative scores" hard constraint.
    """
    rounded = [round(s, 1) for s in scores]
    diff = total_base - sum(rounded)
    rounded[-1] = round(rounded[-1] + diff, 1)
    if rounded[-1] < _MIN_SCORE:
        rounded[-1] = _MIN_SCORE
    return rounded


def _base_points_with_tied_groups(
    raw_base: Sequence[int], times: Sequence[float], n: int
) -> List[float]:
    """Turn the rank-by-rank base points into a per-player list that
    averages tied groups.

    Example: 3 players with the two fastest tied — raw base is
    ``[5, 4, 3]``; tied 1st/2nd both get ``(5+4)/2 = 4.5`` → result
    ``[4.5, 4.5, 3]``. Round total is preserved at 12.
    """
    points = [float(p) for p in raw_base]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and times[j + 1] == times[i]:
            j += 1
        if j > i:
            avg = sum(points[i : j + 1]) / (j - i + 1)
            for k in range(i, j + 1):
                points[k] = avg
        i = j + 1
    return points


def competitive_score(
    players: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Blend rank and time performance into a single score per player.

    Input: a list of ``{"name": str, "time": number}`` dicts for a
    single round of 3, 4, or 5 players. Time is in seconds; lower is
    better. Output: the same players sorted fastest-first, each with a
    ``final_score`` rounded to 1 d.p.

    Ties (same ``time``) share the sum of the positions they'd fill —
    tied 1st/2nd each get ``(5+4)/2 = 4.5``, three-way tie at top get
    ``4.0`` each. When a Case B/C bonus lands on a tied group, the
    bonus is shared (cluster does this naturally via equal
    ``1/time`` weights; clear-winner splits evenly).

    Pipeline (see spec for the full rule set):

    1. Sort by ``time`` ascending and hand out base points by rank.
    2. Average base points across tied groups.
    3. Compute every consecutive time ratio plus the overall
       ``spread = (tn - t1) / t1``.
    4. Classify the round:
         * **Tight** (``spread < 0.5``) — leave base points alone.
         * **Front cluster** (top ``k`` tight + drop > 1.3 to
           position ``k+1``) for ``k`` in 4, 3, 2 (largest first):
           redistribute a bonus pool across the top ``k`` by
           ``1/time`` weights and debit bottom players evenly.
           Pool size scales with the drop: 0.5 pts at drop=1.3,
           1.5 pts at drop=1.6, 2.0 pts at drop≥2.0 — bigger
           blowouts earn bigger pools.
         * **Clear winner** (``r12 > 1.3`` and no cluster matched) —
           award 1st a ``min(2, 2*(r12-1))`` bonus, debit the rest
           proportional to base.
         * Otherwise — no adjustment.
    5. Clamp any cluster-debit-induced negative to 0, scale back to
       the base-points total, round to 1 d.p. with the residue
       absorbed by the last player. Positions 6+ naturally stay at
       0 unless they tie with 5th (only top 5 score; a tied 5th/6th
       shares 0.5 each).

    Rankings never change (sorted input is preserved), scores are
    never negative, and the total is held constant (barring rounding).
    """
    if len(players) < 3:
        raise ValueError(
            f"competitive_score expects at least 3 players, got {len(players)}"
        )
    for p in players:
        if "name" not in p or "time" not in p:
            raise ValueError("each player must have 'name' and 'time' keys")
        if p["time"] <= 0:
            raise ValueError(f"time must be positive (got {p['time']!r})")

    # Step 1 — sort fastest-first.
    sorted_players = sorted(players, key=lambda p: p["time"])
    n = len(sorted_players)
    raw_base = _base_points_for_size(n)
    total_base = sum(raw_base)
    times = [float(p["time"]) for p in sorted_players]

    # Step 2 — seed base points, averaging tied groups so the round
    # total stays at ``total_base`` regardless of how many are tied.
    base_points = _base_points_with_tied_groups(raw_base, times, n)
    scores: List[float] = list(base_points)

    # Step 3 — every consecutive ratio plus the overall spread. The
    # classifier needs the full ratio chain so it can detect top-k
    # clusters of any supported size.
    t1, tn = times[0], times[-1]
    ratios = [times[i + 1] / times[i] for i in range(n - 1)]
    spread = (tn - t1) / t1

    # Step 4 — classify and apply at most one adjustment block. The
    # classifier picks the largest cluster that fits, falling back to
    # clear-winner and finally tight. Cluster bonus pool scales with
    # the size of the post-cluster drop.
    regime, cluster_size, drop_ratio = _classify_round(n, spread, ratios)
    if regime == "cluster":
        assert cluster_size is not None and drop_ratio is not None
        pool = _scaled_cluster_pool(drop_ratio)
        _apply_cluster_bonus(scores, times, n, cluster_size, pool)
    elif regime == "winner":
        _apply_clear_winner_bonus(scores, base_points, times, ratios[0], n)
    # regime == "tight": fall through with base points intact.

    # Step 5 — floor, rebalance, round, reconcile.
    scores = _floor_and_rebalance(scores, total_base, n)
    rounded = _round_and_reconcile(scores, total_base, n)

    return [
        {
            "name": sorted_players[i]["name"],
            "time": sorted_players[i]["time"],
            "final_score": rounded[i],
        }
        for i in range(n)
    ]
