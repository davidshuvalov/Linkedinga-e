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

# Same floor for the "Fastest total time" prize — one flukey sub-minute
# Queens round shouldn't steal a prize meant to reward consistency.
MIN_SUBMISSIONS_FOR_FASTEST_PRIZE = 5

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
    # Lowest cumulative time across time-based games for the week.
    # Same shape as the others so the wrap formatter can share logic.
    fastest_total_time: Optional[PlayerWeeklyStats] = None


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
    ``fastest_total_time`` return ``None`` when nobody meets the
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

    # Fastest total time: lowest cumulative seconds across time-based
    # games. Gate on time_based_submissions so somebody who only played
    # one sub-minute round can't win on volume=1. Tiebreak favours
    # whoever played more rounds (more impressive at the same total).
    fastest_eligible = [
        p for p in leaderboard
        if p.time_based_submissions >= MIN_SUBMISSIONS_FOR_FASTEST_PRIZE
    ]
    fastest_total_time: Optional[PlayerWeeklyStats] = None
    if fastest_eligible:
        fastest_total_time = min(
            fastest_eligible,
            key=lambda p: (p.total_time, -p.time_based_submissions, p.player_id),
        )

    return Prizes(
        most_firsts=most_firsts,
        most_lasts=most_lasts,
        best_average=best_average,
        fastest_total_time=fastest_total_time,
    )


# ---------------------------------------------------------------------------
# competitive_score: rank + time-performance blended scoring
# ---------------------------------------------------------------------------

# Spec thresholds. Named constants so the classification reads like the spec.
_TIGHT_SPREAD_THRESHOLD = 0.5        # Case A: spread < this
_CLUSTER_TOP_RATIO = 1.4             # Case B: r12 and r23 both < this
_CLUSTER_DROP_RATIO = 1.6            # Case B: r34 (or late-drop) > this
_CLEAR_WINNER_RATIO = 1.5            # Case C: r12 > this
_CLUSTER_BONUS_POOL = 1.5            # points redistributed in Case B
_CLEAR_WINNER_MAX_BONUS = 2.0        # hard cap in Case C
_MIN_SCORE = 0.5                     # floor for any player


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


def _classify_round(
    n: int, spread: float, r12: float, r23: Optional[float], r34: Optional[float]
) -> str:
    """Decide which adjustment regime applies (``tight`` / ``cluster`` /
    ``winner``).

    Mirrors the CASE A/B/C tree in the spec: tight beats cluster beats
    winner. Falls back to ``tight`` (no adjustment) whenever none of the
    strong triggers fire — the spec calls out that we only apply
    adjustments "in clear cases".
    """
    if spread < _TIGHT_SPREAD_THRESHOLD:
        return "tight"

    # Front cluster: top 3 ratios are close AND there's a big drop after
    # 3rd place. ``r34`` captures the 3rd-to-4th ratio when a 4th exists;
    # with exactly 3 players there's no "after 3rd" position so cluster
    # can't apply.
    cluster_top_tight = (
        r23 is not None
        and r12 < _CLUSTER_TOP_RATIO
        and r23 < _CLUSTER_TOP_RATIO
    )
    cluster_drop = r34 is not None and r34 > _CLUSTER_DROP_RATIO
    if cluster_top_tight and cluster_drop:
        return "cluster"

    if r12 > _CLEAR_WINNER_RATIO:
        return "winner"

    return "tight"


def _apply_cluster_bonus(
    scores: List[float], times: Sequence[float], n: int
) -> None:
    """Case B — boost the front cluster, debit the stragglers.

    Mutates ``scores`` in place. Top-3 bonuses are time-weighted
    (``1/time_i``, normalised) so the fastest of the cluster gets the
    biggest share. The full bonus pool is then subtracted evenly from
    the bottom players, keeping the running total equal to the base.
    """
    weights = [1.0 / times[i] for i in range(3)]
    wsum = sum(weights)
    for i in range(3):
        scores[i] += _CLUSTER_BONUS_POOL * weights[i] / wsum

    bottom_count = n - 3
    if bottom_count > 0:
        per_player = _CLUSTER_BONUS_POOL / bottom_count
        for i in range(3, n):
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
    """Enforce the 0.5 floor then rebalance the sum back to ``total_base``.

    Flooring can push the total above ``total_base`` (we raised some
    scores without debiting others). A single scale_factor multiply
    across every score restores the invariant without changing
    rankings. Scaling can nudge a floored score back below 0.5, so we
    re-apply the floor once more as a belt-and-braces step — the
    rounding pass afterwards will pin the total exactly.
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
    the slowest player. We clamp that back to the 0.5 floor if needed —
    the hard-constraint summary says min = 0.5 *after* all adjustments.
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
    3. Compute the ratios ``r12``, ``r23``, ``r34`` and the overall
       ``spread = (tn - t1) / t1``.
    4. Classify the round:
         * **Tight** (``spread < 0.5``) — leave base points alone.
         * **Front cluster** (top 3 close + big drop after 3rd) —
           redistribute a 1.5-point bonus pool across the top 3 by
           ``1/time`` weights and debit bottom players evenly.
         * **Clear winner** (``r12 > 1.5``) — award 1st a ``min(2,
           2*(r12-1))`` bonus, debit the rest proportional to base.
         * Otherwise — no adjustment.
    5. Enforce a 0.5 floor, scale back to the base-points total,
       round to 1 d.p. with the residue absorbed by the last player.

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

    # Step 3 — ratios + spread against the fastest time.
    t1, tn = times[0], times[-1]
    r12 = times[1] / t1
    r23 = times[2] / times[1] if n >= 3 else None
    r34 = times[3] / times[2] if n >= 4 else None
    spread = (tn - t1) / t1

    # Step 4 — classify and apply at most one adjustment block.
    regime = _classify_round(n, spread, r12, r23, r34)
    if regime == "cluster":
        _apply_cluster_bonus(scores, times, n)
    elif regime == "winner":
        _apply_clear_winner_bonus(scores, base_points, times, r12, n)
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
