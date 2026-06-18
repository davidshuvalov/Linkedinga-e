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
from datetime import date
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from .db import ScoreRow

# Position → base points. Positions beyond 5 get 0.
_POSITION_POINTS: Dict[int, int] = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1}

# Sentinel raw_score for not-played entries. Must be larger than any real
# score so np players always sort last inside assign_daily_points.
NP_SCORE: int = 999_999

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


def monthly_awards(
    month_scores: Sequence[ScoreRow],
    week_boundaries: Sequence[Tuple[date, date]],
    enabled_games: FrozenSet[str],
) -> Dict[str, Optional["PlayerWeeklyStats"]]:
    """Compute monthly ceremony awards from a full month's scores.

    Returns a dict with keys:
        ``player_of_month``   — most total points across the month
        ``most_consistent``   — lowest std-dev of per-week totals (≥3 weeks)
        ``most_improved``     — steepest positive slope of weekly totals (≥2 weeks)
        ``speedster``         — lowest mean time per time-based round (≥10 subs)

    Values are :class:`PlayerWeeklyStats` instances (winner for each award)
    or ``None`` when the threshold isn't met.
    """
    import statistics

    filtered = [s for s in month_scores if s.game in enabled_games]
    if not filtered:
        return {
            "player_of_month": None,
            "most_consistent": None,
            "most_improved": None,
            "speedster": None,
        }

    # Overall month leaderboard → Player of the Month.
    month_lb = weekly_leaderboard(filtered)
    player_of_month: Optional[PlayerWeeklyStats] = month_lb[0] if month_lb else None

    # Per-player weekly totals for consistency + trend.
    pid_week_totals: Dict[int, List[float]] = {}
    for mon, sun in week_boundaries:
        week_sc = [s for s in filtered if mon <= s.puzzle_date <= sun]
        if not week_sc:
            continue
        wlb = weekly_leaderboard(week_sc)
        for entry in wlb:
            pid_week_totals.setdefault(entry.player_id, []).append(entry.total_points)

    most_consistent: Optional[PlayerWeeklyStats] = None
    most_consistent_std = float("inf")
    most_improved: Optional[PlayerWeeklyStats] = None
    most_improved_slope = float("-inf")

    pid_to_stats = {e.player_id: e for e in month_lb}

    for pid, totals in pid_week_totals.items():
        if pid not in pid_to_stats:
            continue
        if len(totals) >= 3:
            std = statistics.stdev(totals)
            if std < most_consistent_std:
                most_consistent_std = std
                most_consistent = pid_to_stats[pid]

        if len(totals) >= 2:
            n = len(totals)
            xs = list(range(n))
            mean_x = sum(xs) / n
            mean_y = sum(totals) / n
            num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, totals))
            den = sum((x - mean_x) ** 2 for x in xs)
            slope = num / den if den != 0 else 0.0
            if slope > most_improved_slope:
                most_improved_slope = slope
                most_improved = pid_to_stats[pid]

    # Speedster: lowest average time across time-based games, ≥10 submissions.
    speedster: Optional[PlayerWeeklyStats] = None
    speedster_avg = float("inf")
    for entry in month_lb:
        if (
            entry.time_based_submissions >= 10
            and entry.average_time < speedster_avg
        ):
            speedster_avg = entry.average_time
            speedster = entry

    # Only award most_improved if slope is actually positive.
    if most_improved_slope <= 0:
        most_improved = None

    return {
        "player_of_month": player_of_month,
        "most_consistent": most_consistent,
        "most_improved": most_improved,
        "speedster": speedster,
    }


def estimate_score_to_beat(
    target_points: float,
    competitors: Sequence[int],
    game: str,
    *,
    max_raw: int = 86_400,
) -> Optional[int]:
    """Binary-search for the smallest raw_score that earns >= ``target_points``
    when submitted alongside ``competitors`` in ``game``.

    Returns ``None`` when even raw_score=1 can't reach ``target_points``
    (field is too large / points too high) or when ``game`` is Pinpoint
    (guess-based — can't meaningfully estimate a target time).
    """
    if game in _NON_TIME_GAMES:
        return None
    if not competitors:
        return None

    def _pts_for(raw: int) -> float:
        from .db import ScoreRow
        from datetime import date as _date
        sentinel_id = -1
        rows: List[ScoreRow] = [
            ScoreRow(
                player_id=sentinel_id,
                player_name="?",
                game=game,
                puzzle_no=0,
                puzzle_date=_date(2000, 1, 1),
                raw_score=raw,
                share_text="",
            )
        ] + [
            ScoreRow(
                player_id=i,
                player_name=f"p{i}",
                game=game,
                puzzle_no=0,
                puzzle_date=_date(2000, 1, 1),
                raw_score=c,
                share_text="",
            )
            for i, c in enumerate(competitors)
        ]
        pts = assign_daily_points(rows)
        return pts.get(sentinel_id, 0.0)

    # If even score=1 can't reach target, it's impossible.
    if _pts_for(1) < target_points:
        return None

    lo, hi = 1, max_raw
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _pts_for(mid) >= target_points:
            lo = mid
        else:
            hi = mid - 1

    return lo if _pts_for(lo) >= target_points else None


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
    Entries with ``is_np=True`` (not-played sentinels) are separated
    before dispatch and assigned points for the positions they fill
    after all real players — plain average, same as tied_points.
    """
    if not scores:
        return {}

    real = [s for s in scores if not s.is_np]
    np_list = [s for s in scores if s.is_np]

    if not real:
        return {}

    sorted_real = sorted(real, key=lambda s: s.raw_score)
    n_real = len(sorted_real)

    # Pinpoint is guess count (1–5); the ratio/spread model the
    # competitive algorithm uses would treat 1 vs 2 guesses as a 2x
    # "time" difference which is nonsense. Route all pinpoint rounds
    # through the legacy rank system.
    pinpoint_free = all(s.game not in _NON_TIME_GAMES for s in sorted_real)
    big_enough = n_real >= 3

    if pinpoint_free and big_enough:
        players = [
            {"name": s.player_name, "time": s.raw_score}
            for s in sorted_real
        ]
        results = competitive_score(players)
        result: Dict[int, float] = {
            sorted_real[i].player_id: results[i]["final_score"]
            for i in range(n_real)
        }
    else:
        result = _legacy_rank_points(sorted_real)

    if np_list:
        np_pts = _tied_points(n_real + 1, len(np_list))
        for s in np_list:
            result[s.player_id] = np_pts

    return result


# ---------------------------------------------------------------------------
# weekly_leaderboard
# ---------------------------------------------------------------------------


def _with_np_entries(
    group_scores: List[ScoreRow],
    active_players: Optional[Dict[int, str]],
) -> List[ScoreRow]:
    """Return ``group_scores`` augmented with not-played sentinels.

    For every player in ``active_players`` who is absent from
    ``group_scores``, append a virtual :class:`ScoreRow` with
    ``is_np=True`` and ``raw_score=NP_SCORE``.  Returns the original
    list unchanged when ``active_players`` is ``None`` or empty.
    """
    if not active_players:
        return group_scores
    played = {s.player_id for s in group_scores}
    first = group_scores[0]
    np_rows = [
        ScoreRow(
            player_id=pid,
            player_name=name,
            game=first.game,
            puzzle_no=first.puzzle_no,
            puzzle_date=first.puzzle_date,
            raw_score=NP_SCORE,
            is_np=True,
        )
        for pid, name in active_players.items()
        if pid not in played
    ]
    return list(group_scores) + np_rows


def weekly_leaderboard(
    scores: Sequence[ScoreRow],
    active_players: Optional[Dict[int, str]] = None,
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
        augmented = _with_np_entries(group_scores, active_players)
        for pid, pts in assign_daily_points(augmented).items():
            totals[pid] += pts
        # First / last only count when there's real competition — a lone
        # submitter would otherwise sweep both metrics absurdly.
        # Only real (non-np) entries are considered so np players don't
        # "steal" the last-place slot from whoever actually played worst.
        real_in_group = [s for s in group_scores if not s.is_np]
        if len(real_in_group) < 2:
            continue
        best_raw = min(s.raw_score for s in real_in_group)
        worst_raw = max(s.raw_score for s in real_in_group)
        for s in real_in_group:
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
                                     # positions 6+ are pinned to 0
                                     # by _apply_floors_and_ceiling

# Per-position minimum floors enforced after the bonus/debit pipeline.
# Index 0 is 1st (no floor — bonused naturally), 1 is 2nd, … 4 is 5th.
# Positions 5+ are pinned to 0.0 as a hard ceiling, not a floor: a 6th
# (or worse) finisher always scores 0, breaking the historical
# "tied 5th/6th share 0.5 each" convention.
_POSITION_FLOORS: List[float] = [0.0, 3.0, 2.0, 1.0, 0.5]

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

# Absolute-time tie floor. Pure ratio-based clustering misfires on
# very fast rounds: 4s vs 6s is a 1.5x ratio that looks like a
# runaway winner, but in real-world terms the players are tied.
# When both times are inside the floor and the gap is small, we
# treat the pair as in-cluster regardless of the ratio.
_ABSOLUTE_TIE_FLOOR = 10.0   # seconds: "objectively fast" zone
_ABSOLUTE_TIE_DELTA = 3.0    # seconds: gaps ≤ this in the floor zone count as tied


def _is_absolute_tie(t1: float, t2: float) -> bool:
    """True when ``t1``/``t2`` are both fast and close in absolute terms.

    Used by cluster detection to recognise a top pack like ``[4, 6, 7]``
    that the pure ratio test would reject (``r12 = 1.5`` exceeds the
    1.4 in-cluster threshold).
    """
    return t1 < _ABSOLUTE_TIE_FLOOR and (t2 - t1) <= _ABSOLUTE_TIE_DELTA


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
    times: Sequence[float], ratios: Sequence[float], n: int
) -> Optional[Tuple[int, float]]:
    """Return ``(cluster_size, drop_ratio)`` when the round has a
    "top-k tight pack with a clear drop to position k+1" shape, else
    ``None``.

    ``ratios[i]`` is ``times[i+1] / times[i]`` (so ``ratios[0]`` is
    r12). We probe the largest cluster first so a top-4 round doesn't
    silently match the top-3 rule and leave the 4th-placed player out
    of the bonus pack.

    A cluster of size ``k`` requires:

    * All ``k - 1`` in-cluster pairs are tight — either by ratio
      (``< 1.4``) or by absolute-time tie (both fast, gap small).
      The absolute-tie escape hatch keeps shapes like ``[4, 6, 7]``
      from being misread as a runaway 1st.
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
            ratios[i] < _CLUSTER_TOP_RATIO
            or _is_absolute_tie(times[i], times[i + 1])
            for i in range(k - 1)
        )
        drop_ratio = ratios[k - 1]
        if in_cluster_tight and drop_ratio > _CLUSTER_DROP_RATIO:
            return k, drop_ratio
    return None


def _classify_round(
    n: int, spread: float, times: Sequence[float], ratios: Sequence[float]
) -> Tuple[str, Optional[int], Optional[float]]:
    """Decide which adjustment regime applies.

    Returns ``(regime, cluster_size, drop_ratio)`` where ``regime``
    is one of ``"tight"`` / ``"cluster"`` / ``"winner"``. The
    ``cluster_size`` and ``drop_ratio`` are populated only when
    ``regime == "cluster"``; otherwise both are ``None``.

    Order of precedence (largest cluster wins, then clear-winner,
    then tight). Tight short-circuits on a small overall spread —
    the spec's "only adjust in clear cases" rule. The clear-winner
    fallback also defers when 1st/2nd are an absolute tie (both
    inside the fast-zone floor with a small gap), since a 4s vs 6s
    "win" is noise, not a runaway lead.
    """
    if spread < _TIGHT_SPREAD_THRESHOLD:
        return "tight", None, None

    detected = _detect_cluster(times, ratios, n)
    if detected is not None:
        cluster_size, drop_ratio = detected
        return "cluster", cluster_size, drop_ratio

    if ratios[0] > _CLEAR_WINNER_RATIO and not _is_absolute_tie(times[0], times[1]):
        return "winner", None, None

    return "tight", None, None


def _adaptive_straggler_weights(distances: Sequence[float]) -> List[float]:
    """Blend distance-from-boundary with even-split, adapting to spread.

    Pure distance weighting is right when one straggler is genuinely
    far off the back of the field (Zip-style ``[13, 51]`` past a 7s
    boundary), but too harsh when stragglers are bunched together
    (Tango-style ``[34, 41]`` past a 22s boundary — only 7s separates
    them). We measure spread as ``(max - min) / max`` of the
    distances and use that as the blend factor:

    * spread → 1 (one far outlier): nearly pure distance weighting,
      so the outlier absorbs most of the debit.
    * spread → 0 (bunched stragglers): nearly even split, so 4th
      and 5th lose roughly the same.

    Returns one weight per straggler; caller normalises before
    applying. Empty input returns ``[]``.
    """
    if not distances:
        return []
    if len(distances) == 1 or max(distances) <= 0:
        return list(distances)
    alpha = (max(distances) - min(distances)) / max(distances)
    avg = sum(distances) / len(distances)
    return [alpha * d + (1.0 - alpha) * avg for d in distances]


def _apply_cluster_bonus(
    scores: List[float],
    base_points: Sequence[float],
    times: Sequence[float],
    n: int,
    top_k: int,
    pool: float,
) -> None:
    """Boost the front ``top_k`` cluster, debit the stragglers.

    Mutates ``scores`` in place. In-cluster bonuses are time-weighted
    (``1/time_i``, normalised) so the fastest of the cluster gets the
    biggest share. The bonus pool is then subtracted from the
    stragglers using :func:`_adaptive_straggler_weights` — a blend of
    distance-from-boundary and even split that adapts to how
    spread-out the stragglers are. Only stragglers with non-zero base
    points participate in the debit (positions 6+ have base 0 and are
    spec'd to stay at 0). ``pool`` is computed by
    :func:`_scaled_cluster_pool` from the post-cluster drop ratio.
    """
    weights = [1.0 / times[i] for i in range(top_k)]
    wsum = sum(weights)
    for i in range(top_k):
        scores[i] += pool * weights[i] / wsum

    boundary = times[top_k - 1]
    straggler_indices = [i for i in range(top_k, n) if base_points[i] > 0]
    if not straggler_indices:
        return

    distances = [times[i] - boundary for i in straggler_indices]
    blended = _adaptive_straggler_weights(distances)
    bsum = sum(blended)
    if bsum > 0:
        for i, w in zip(straggler_indices, blended):
            scores[i] -= pool * w / bsum
    else:
        # Defensive: drop_ratio > 1.3 guarantees bsum > 0, but if a
        # caller ever bypasses that, fall back to even debit.
        per_player = pool / len(straggler_indices)
        for i in straggler_indices:
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
    then subtracted from the other players using
    :func:`_adaptive_straggler_weights` — distance-from-winner blended
    with even-split, so a far outlier absorbs most of the debit but
    bunched stragglers split it more evenly.

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

    winner_time = times[0]
    debit_indices = [i for i in range(tied_with_1st, n) if base_points[i] > 0]
    distances = [times[i] - winner_time for i in debit_indices]
    blended = _adaptive_straggler_weights(distances)
    bsum = sum(blended)
    if bsum > 0:
        for i, w in zip(debit_indices, blended):
            scores[i] -= bonus * w / bsum
    else:
        # Defensive: r12 > 1.3 guarantees the runner-up is strictly
        # slower, so bsum > 0. Fall back to base-weighted debit
        # only if a degenerate input slips through.
        other_base_sum = sum(base_points[i] for i in debit_indices)
        if other_base_sum > 0:
            for i in debit_indices:
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
    scores: Sequence[float],
    base_points: Sequence[float],
    total_base: int,
    n: int,
) -> List[float]:
    """Round each score to 1 d.p. and absorb the residue on the last
    *scoring* row.

    Rounding independently drifts the sum off ``total_base`` by up to
    ``n * 0.05``. The residue lands on the lowest-ranked player whose
    ``base_points`` is non-zero — never on a 6th-or-worse finisher,
    who must stay at exactly 0 (see :func:`_apply_floors_and_ceiling`).
    Falls back to the absolute last index if every player has base 0
    (degenerate, but defensive).
    """
    rounded = [round(s, 1) for s in scores]
    target = n - 1
    while target >= 0 and base_points[target] == 0:
        target -= 1
    if target < 0:
        target = n - 1
    diff = total_base - sum(rounded)
    rounded[target] = round(rounded[target] + diff, 1)
    if rounded[target] < _MIN_SCORE:
        rounded[target] = _MIN_SCORE
    return rounded


def _equalize_tied_groups(
    scores: List[float], times: Sequence[float], n: int
) -> List[float]:
    """Average the final scores within each tied-time group.

    After rounding and reconciliation, two players who finished at the
    same time can end up with different scores (e.g. 1.1 vs 1.0) because
    ``_round_and_reconcile`` drops the rounding residue on only the last
    player in the group. This pass re-averages every tied group so they
    come out equal.  The round total may drift by at most 0.05 * group_size
    but the per-player fairness invariant is more important than exact sums.
    """
    result = list(scores)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and times[j + 1] == times[i]:
            j += 1
        if j > i:
            group = result[i : j + 1]
            if len(set(group)) > 1:
                avg = sum(group) / len(group)
                equal_score = round(avg, 1)
                for k in range(i, j + 1):
                    result[k] = equal_score
        i = j + 1
    return result


def _apply_floors_and_ceiling(
    rounded: List[float], times: Sequence[float], n: int
) -> List[float]:
    """Pin 6th+ to 0 and bump 2nd–5th up to their per-position minimums.

    Floors (by 0-indexed position): 1st=none, 2nd=3.0, 3rd=2.0,
    4th=1.0, 5th=0.5. Positions strictly past 5th are pinned to 0
    as a hard ceiling — a 6th finisher who's slower than the 5th
    finisher always scores 0.

    Ties on time are honoured: any position-6+ player whose time
    matches the 5th-placed player is treated as co-5th and inherits
    5th's 0.5 floor, so a tied 5th/6th pair both keep 0.5. Tied
    groups inside the resulting scoring window share an averaged
    floor so tied players come out equal — e.g. tied 4th/5th both
    get floor (1.0 + 0.5) / 2 = 0.75.

    Bumps land on top of the existing scores (never lowering anyone),
    so the round total can drift up by the cumulative bump. The
    leaderboard tolerates that drift; the floors matter more than
    invariant sums for the "5th place isn't beaten by 6th" guarantee.
    """
    # Scoring window extends past 5th (index 4) only as far as the
    # tie chain reaches. Anyone past that window is strictly slower
    # than 5th and pins to 0.
    scoring_end = min(n, 5)
    while scoring_end < n and times[scoring_end] == times[4]:
        scoring_end += 1

    floors = [0.0] * n
    for i in range(min(n, 5)):
        floors[i] = _POSITION_FLOORS[i]
    # Tied co-5th players inherit the 5th-place floor.
    for i in range(5, scoring_end):
        floors[i] = _POSITION_FLOORS[4]

    # Average floors across tied groups inside the scoring window.
    i = 0
    while i < scoring_end:
        j = i
        while j + 1 < scoring_end and times[j + 1] == times[i]:
            j += 1
        if j > i:
            avg = sum(floors[i : j + 1]) / (j - i + 1)
            for k in range(i, j + 1):
                floors[k] = avg
        i = j + 1

    result = list(rounded)
    for i in range(n):
        if i >= scoring_end:
            result[i] = 0.0
        elif result[i] < floors[i]:
            result[i] = round(floors[i], 1)
    return result


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
       absorbed by the last *scoring* player (a 6th finisher never
       picks up residue).
    6. Pin 6th+ to exactly 0 and bump 2nd–5th up to per-position
       minimums (3.0 / 2.0 / 1.0 / 0.5). A 6th-placed player tied on
       time with 5th is treated as co-5th and shares the 0.5 floor
       (e.g. tied 5th/6th both get 0.5); only strictly-slower 6th+
       finishers pin to 0. Tied groups inside the scoring window
       share averaged floors so tied players stay equal.

    Rankings never change (sorted input is preserved) and scores are
    never negative. The round total is held constant by the bonus
    pipeline but the final floor step can drift it upward by the
    cumulative bump applied to under-floor positions.
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
    regime, cluster_size, drop_ratio = _classify_round(n, spread, times, ratios)
    if regime == "cluster":
        assert cluster_size is not None and drop_ratio is not None
        pool = _scaled_cluster_pool(drop_ratio)
        _apply_cluster_bonus(scores, base_points, times, n, cluster_size, pool)
    elif regime == "winner":
        _apply_clear_winner_bonus(scores, base_points, times, ratios[0], n)
    # regime == "tight": fall through with base points intact.

    # Step 5 — floor, rebalance, round, reconcile, then apply
    # per-position floors and the 6th+ zero ceiling.
    scores = _floor_and_rebalance(scores, total_base, n)
    rounded = _round_and_reconcile(scores, base_points, total_base, n)
    rounded = _apply_floors_and_ceiling(rounded, times, n)
    rounded = _equalize_tied_groups(rounded, times, n)

    return [
        {
            "name": sorted_players[i]["name"],
            "time": sorted_players[i]["time"],
            "final_score": rounded[i],
        }
        for i in range(n)
    ]
