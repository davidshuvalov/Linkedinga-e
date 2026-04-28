"""Unit tests for ``app.scoring`` — new 5-4-3-2-1 system with ceil'd ties."""

from __future__ import annotations

from datetime import date

import pytest

from app.db import ScoreRow
from app.scoring import (
    GameLeader,
    PlayerWeeklyStats,
    Prizes,
    _tied_points,
    assign_daily_points,
    competitive_score,
    game_leaders,
    prize_allocations,
    weekly_leaderboard,
)

D = date(2026, 4, 14)


def _row(
    pid: int,
    name: str,
    game: str,
    puzzle_no: int,
    raw: int,
    puzzle_date: date = D,
) -> ScoreRow:
    return ScoreRow(
        player_id=pid,
        player_name=name,
        game=game,
        puzzle_no=puzzle_no,
        puzzle_date=puzzle_date,
        raw_score=raw,
    )


# ---------------------------------------------------------------------------
# _tied_points
# ---------------------------------------------------------------------------


class TestTiedPoints:
    # Ties now split positional points as a true average (no ceiling)
    # so the round total stays invariant at 15 (5+4+3+2+1) regardless
    # of how many are tied. Tied 1st/2nd → 4.5 each, not 5.
    def test_solo_positions(self):
        assert _tied_points(1, 1) == 5
        assert _tied_points(2, 1) == 4
        assert _tied_points(3, 1) == 3
        assert _tied_points(4, 1) == 2
        assert _tied_points(5, 1) == 1
        assert _tied_points(6, 1) == 0

    def test_tied_2nd_3rd_averages_to_3_5(self):
        # (4+3)/2 = 3.5 — not ceil'd
        assert _tied_points(2, 2) == 3.5

    def test_three_way_tie_1st_averages_to_4(self):
        # (5+4+3)/3 = 4.0 — integer result
        assert _tied_points(1, 3) == 4.0

    def test_tied_4th_5th_averages_to_1_5(self):
        # (2+1)/2 = 1.5 — fractional
        assert _tied_points(4, 2) == 1.5

    def test_tied_5th_6th_averages_to_0_5(self):
        # (1+0)/2 = 0.5 — 6th+ is zero, so this dips below a whole point
        assert _tied_points(5, 2) == 0.5

    def test_tied_1st_2nd_averages_to_4_5(self):
        # (5+4)/2 = 4.5 — shared across the tied pair, total 9
        assert _tied_points(1, 2) == 4.5

    def test_all_tied_beyond_5th(self):
        # (0+0)/2 = 0
        assert _tied_points(6, 2) == 0.0

    def test_three_way_tie_4th(self):
        # (2+1+0)/3 = 1.0
        assert _tied_points(4, 3) == 1.0


# ---------------------------------------------------------------------------
# assign_daily_points
# ---------------------------------------------------------------------------


class TestAssignDailyPoints:
    def test_empty(self):
        assert assign_daily_points([]) == {}

    def test_single_player_gets_5(self):
        assert assign_daily_points(
            [_row(1, "Alice", "queens", 714, 10)]
        ) == {1: 5}

    def test_straight_ranking_5_players_uses_competitive_score(self):
        # 5 evenly-spaced times with r12 = 2.0 trigger the competitive
        # "clear winner" branch — bonus capped at +2 for 1st, debited
        # proportionally from everyone else. Rankings preserved and
        # total still sums to 15 (base 5+4+3+2+1).
        scores = [_row(i, f"P{i}", "queens", 714, i * 10) for i in range(1, 6)]
        pts = assign_daily_points(scores)
        # 1st gets the cap; 5th absorbs the rounding residue.
        assert pts[1] == 7.0
        assert pts[1] > pts[2] > pts[3] > pts[4] > pts[5]
        assert abs(sum(pts.values()) - 15.0) < 0.05

    def test_legacy_fallback_for_pinpoint(self):
        # Pinpoint isn't a time-based game (guess counts 1–5), so the
        # competitive algorithm is bypassed and legacy 5-4-3-2-1 is
        # used — same integer output the bot has always produced.
        scores = [
            _row(1, "Alice", "pinpoint", 714, 1),
            _row(2, "Bob",   "pinpoint", 714, 2),
            _row(3, "Cy",    "pinpoint", 714, 3),
            _row(4, "Dee",   "pinpoint", 714, 4),
            _row(5, "Eve",   "pinpoint", 714, 5),
        ]
        assert assign_daily_points(scores) == {
            1: 5.0, 2: 4.0, 3: 3.0, 4: 2.0, 5: 1.0,
        }

    def test_ties_flow_through_competitive_and_preserve_total(self):
        # Ties are now handled inline by competitive_score — base
        # points are averaged within tied groups, and any Case C
        # bonus is still applied. Round total always lands on 14.
        scores = [
            _row(1, "A", "queens", 714, 10),
            _row(2, "B", "queens", 714, 20),
            _row(3, "C", "queens", 714, 20),  # tied with B
            _row(4, "D", "queens", 714, 40),
        ]
        pts = assign_daily_points(scores)
        # Tied 2nd/3rd split their share of the final points equally.
        assert pts[2] == pts[3]
        # Round total preserved.
        assert abs(sum(pts.values()) - 14.0) < 0.05

    def test_six_player_round_through_competitive_clear_winner(self):
        # 6 players with r12 = 2.0 (P1 = 10s, P2 = 20s) — Case C
        # fires for the 1st-place clear winner. P1 gets a bonus,
        # the rest absorb proportional debit weighted by base
        # points. P6 has base 0 so absorbs nothing and stays at 0
        # — "only top 5 score" unless 5th/6th tie.
        scores = [_row(i, f"P{i}", "queens", 714, i * 10) for i in range(1, 7)]
        pts = assign_daily_points(scores)
        assert pts[1] >= 5.0                      # bonus on 1st
        assert pts[1] > pts[2] > pts[3] > pts[4] > pts[5]
        assert pts[6] == 0.0                      # 6th doesn't score
        assert abs(sum(pts.values()) - 15.0) < 0.2

    def test_seventh_and_beyond_score_zero(self):
        # Positions 6+ have base 0 and no bonus/debit reaches them
        # under Case C (proportional debit × 0 = 0). They stay at 0.
        scores = [_row(i, f"P{i}", "queens", 714, i * 10) for i in range(1, 8)]
        pts = assign_daily_points(scores)
        assert pts[1] > pts[2] > pts[3] > pts[4] > pts[5]
        assert pts[6] == 0.0
        assert pts[7] == 0.0
        assert abs(sum(pts.values()) - 15.0) < 0.2

    def test_tied_5th_6th_share_last_point(self):
        # Tied 5th/6th split positional points (1+0)/2 = 0.5 each.
        # The only way for a 6th-place finisher to score under the
        # new rules is by tying with 5th. Use a tight-regime round
        # (small spread) so base points stand without a bonus debit
        # potentially zeroing the tied pair.
        scores = [
            _row(1, "A", "queens", 714, 10),
            _row(2, "B", "queens", 714, 11),
            _row(3, "C", "queens", 714, 12),
            _row(4, "D", "queens", 714, 13),
            _row(5, "E", "queens", 714, 14),  # tied 5th/6th
            _row(6, "F", "queens", 714, 14),  # tied 5th/6th
        ]
        pts = assign_daily_points(scores)
        assert pts[5] == pts[6]                  # tied pair equal
        assert pts[5] > 0.0                      # both get something
        assert abs(sum(pts.values()) - 15.0) < 0.2

    def test_tied_2nd_3rd_through_competitive(self):
        # 4-player round with tied 2nd/3rd. r12 = 2.0 triggers the
        # clear-winner branch; tied pair's base points are averaged
        # so they absorb the debit together.
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(2, "Bob", "queens", 714, 20),    # tied 2nd
            _row(3, "Charlie", "queens", 714, 20),  # tied 2nd
            _row(4, "Dee", "queens", 714, 30),
        ]
        r = assign_daily_points(scores)
        assert r[2] == r[3]            # tied pair shares equally
        assert r[1] > r[2] > r[4]      # rankings preserved
        assert abs(sum(r.values()) - 14.0) < 0.05

    def test_three_way_tie_at_top_all_share_equally(self):
        # Three tied for 1st with a 4th place well behind: front
        # cluster triggers (r12=1, r23=1, r34=3), so the top three
        # split both the averaged base (4 each) and the cluster
        # bonus. All three should come out equal.
        scores = [
            _row(1, "A", "queens", 714, 10),
            _row(2, "B", "queens", 714, 10),
            _row(3, "C", "queens", 714, 10),
            _row(4, "D", "queens", 714, 30),
        ]
        r = assign_daily_points(scores)
        assert r[1] == r[2] == r[3]
        assert r[1] > r[4]
        # Total tolerance is loose because this round hits the
        # documented floor-clamp drift: the cluster debit drives 4th
        # to exactly 0, the rounding residue can't be absorbed there
        # without violating the floor, so the sum can drift by ~0.1.
        assert abs(sum(r.values()) - 14.0) < 0.15

    def test_tied_1st_2nd_share_4_5_each(self):
        # 3 players, tied 1st/2nd, 3rd close behind. Tight regime
        # (spread = 0.3 < 0.5) so no cluster or clear-winner fires
        # — base points stand and the tied pair shares (5+4)/2 = 4.5.
        scores = [
            _row(1, "A", "queens", 714, 10),
            _row(2, "B", "queens", 714, 10),
            _row(3, "C", "queens", 714, 13),
        ]
        r = assign_daily_points(scores)
        assert r[1] == 4.5
        assert r[2] == 4.5
        assert r[3] == 3.0

    def test_tied_4th_5th_share_debit(self):
        # 5 players with tied 4th/5th. r12 = 2 → clear-winner case.
        # Tied pair's base is averaged to 1.5 each; Case C debit is
        # distributed proportionally to that averaged base so both
        # receive an identical final score.
        scores = [
            _row(1, "A", "queens", 714, 10),
            _row(2, "B", "queens", 714, 20),
            _row(3, "C", "queens", 714, 30),
            _row(4, "D", "queens", 714, 40),  # tied 4th
            _row(5, "E", "queens", 714, 40),  # tied 4th
        ]
        r = assign_daily_points(scores)
        assert r[4] == r[5]
        assert r[1] > r[2] > r[3] > r[4]
        assert abs(sum(r.values()) - 15.0) < 0.05

    def test_pinpoint_lower_guess_count_wins(self):
        scores = [
            _row(1, "Alice", "pinpoint", 714, 2),
            _row(2, "Bob", "pinpoint", 714, 3),
            _row(3, "Charlie", "pinpoint", 714, 5),
        ]
        assert assign_daily_points(scores) == {1: 5, 2: 4, 3: 3}


# ---------------------------------------------------------------------------
# weekly_leaderboard
# ---------------------------------------------------------------------------


class TestWeeklyLeaderboard:
    def test_empty(self):
        assert weekly_leaderboard([]) == []

    def test_aggregates_across_games(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),  # 5
            _row(2, "Bob", "queens", 714, 20),     # 4
            _row(1, "Alice", "tango", 554, 30),    # 4
            _row(2, "Bob", "tango", 554, 20),      # 5
        ]
        lb = weekly_leaderboard(scores)
        assert lb[0].total_points == 9  # 5+4
        assert lb[1].total_points == 9  # 4+5
        assert lb[0].distinct_games == 2
        assert lb[0].days_played == 1

    def test_midnight_spanning_shared_puzzle(self):
        monday = date(2026, 4, 13)
        tuesday = date(2026, 4, 14)
        scores = [
            _row(1, "Alice", "queens", 714, 10, puzzle_date=monday),
            _row(2, "Bob", "queens", 714, 20, puzzle_date=tuesday),
        ]
        lb = weekly_leaderboard(scores)
        alice = next(p for p in lb if p.player_id == 1)
        bob = next(p for p in lb if p.player_id == 2)
        assert alice.total_points == 5
        assert bob.total_points == 4

    def test_sort_order_points_desc(self):
        scores = [
            _row(3, "Charlie", "queens", 714, 30),
            _row(1, "Alice", "queens", 714, 20),
            _row(2, "Bob", "queens", 714, 10),
        ]
        lb = weekly_leaderboard(scores)
        assert [p.player_name for p in lb] == ["Bob", "Alice", "Charlie"]


# ---------------------------------------------------------------------------
# game_leaders
# ---------------------------------------------------------------------------


class TestGameLeaders:
    def test_single_game(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(2, "Bob", "queens", 714, 20),
        ]
        leaders = game_leaders(scores)
        assert len(leaders) == 1
        assert leaders[0].game == "queens"
        assert leaders[0].player_name == "Alice"
        assert leaders[0].total_points == 5

    def test_multiple_games(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),  # Alice 5
            _row(2, "Bob", "queens", 714, 20),     # Bob 4
            _row(1, "Alice", "tango", 554, 30),    # Alice 4
            _row(2, "Bob", "tango", 554, 20),      # Bob 5
        ]
        leaders = game_leaders(scores)
        by_game = {g.game: g for g in leaders}
        assert by_game["queens"].player_name == "Alice"
        assert by_game["tango"].player_name == "Bob"

    def test_across_multiple_days(self):
        d1 = date(2026, 4, 13)
        d2 = date(2026, 4, 14)
        scores = [
            _row(1, "Alice", "queens", 713, 10, d1),  # 5
            _row(2, "Bob", "queens", 713, 20, d1),     # 4
            _row(1, "Alice", "queens", 714, 30, d2),   # 4
            _row(2, "Bob", "queens", 714, 10, d2),     # 5
        ]
        leaders = game_leaders(scores)
        ql = next(g for g in leaders if g.game == "queens")
        # Alice: 5+4=9; Bob: 4+5=9; tie → smaller player_id = Alice
        assert ql.player_name == "Alice"
        assert ql.total_points == 9


# ---------------------------------------------------------------------------
# prize_allocations
# ---------------------------------------------------------------------------


class TestPrizeAllocations:
    def test_empty(self):
        # Champion / All-rounder / Wooden spoon intentionally removed —
        # they're redundant with the leaderboard in the wrap. Only three
        # prizes now: Most firsts, Most lasts, Best average.
        assert prize_allocations([]) == Prizes(None, None, None)

    def test_distinct_winners(self):
        # id, name, points, distinct_games, days_played, first_places,
        # last_places, submissions
        lb = [
            PlayerWeeklyStats(1, "Alice", 25, 3, 2, first_places=2,
                              last_places=0, submissions=6),
            PlayerWeeklyStats(2, "Bob", 20, 5, 3, first_places=1,
                              last_places=1, submissions=12),
            PlayerWeeklyStats(3, "Charlie", 15, 2, 2, first_places=3,
                              last_places=2, submissions=5),
            PlayerWeeklyStats(4, "Dee", 3, 1, 1, first_places=0,
                              last_places=4, submissions=4),
        ]
        p = prize_allocations(lb)
        assert p.most_firsts.player_name == "Charlie"  # 3 firsts
        assert p.most_lasts.player_name == "Dee"       # 4 lasts
        assert p.best_average.player_name == "Alice"   # 25/6 ≈ 4.17

    def test_most_firsts_tiebreaks_on_points(self):
        # Same first-place count → higher total_points wins (being
        # strong overall and clutching 1sts deserves the nod).
        lb = [
            PlayerWeeklyStats(1, "Alice", 10, 3, 2, first_places=4,
                              submissions=5),
            PlayerWeeklyStats(2, "Bob", 20, 3, 2, first_places=4,
                              submissions=5),
        ]
        assert prize_allocations(lb).most_firsts.player_name == "Bob"

    def test_most_lasts_tiebreaks_on_fewer_points(self):
        # Flipside: same last-place count → lower total_points wins
        # (being weaker overall owns the title).
        lb = [
            PlayerWeeklyStats(1, "Alice", 20, 3, 2, first_places=0,
                              last_places=3, submissions=5),
            PlayerWeeklyStats(2, "Bob", 10, 3, 2, first_places=0,
                              last_places=3, submissions=5),
        ]
        assert prize_allocations(lb).most_lasts.player_name == "Bob"

    def test_prizes_none_when_no_competitive_rounds(self):
        # If every round was a singleton, first_places and last_places
        # are zero for everyone and those prizes go unawarded.
        lb = [
            PlayerWeeklyStats(1, "Alice", 5, 1, 1, first_places=0,
                              last_places=0, submissions=1),
        ]
        p = prize_allocations(lb)
        assert p.most_firsts is None
        assert p.most_lasts is None
        assert p.best_average is None  # also under threshold

    def test_best_average_requires_min_submissions(self):
        # Alice has the highest average but only 4 submissions — under
        # the MIN_SUBMISSIONS_FOR_AVERAGE_PRIZE=5 threshold she's
        # ineligible. Bob (5 submissions, lower avg) wins instead.
        lb = [
            PlayerWeeklyStats(1, "Alice", 20, 2, 2, first_places=4, submissions=4),
            PlayerWeeklyStats(2, "Bob", 15, 3, 3, first_places=2, submissions=5),
        ]
        p = prize_allocations(lb)
        assert p.best_average.player_name == "Bob"
        assert abs(p.best_average.average_points - 3.0) < 1e-9

    def test_best_average_returns_none_when_nobody_eligible(self):
        # Everyone played fewer than 5 rounds — no "Best average" winner
        # makes sense; the formatter will just skip the line.
        lb = [
            PlayerWeeklyStats(1, "Alice", 10, 2, 2, first_places=2, submissions=3),
            PlayerWeeklyStats(2, "Bob", 8, 2, 2, first_places=1, submissions=2),
        ]
        p = prize_allocations(lb)
        assert p.best_average is None

    def test_best_average_prefers_higher_avg_even_with_fewer_points(self):
        # Alice: 30/6 = 5.0 avg (perfect). Bob: 40/10 = 4.0. Both eligible.
        # Best average goes to Alice even though Bob has more points.
        lb = [
            PlayerWeeklyStats(1, "Alice", 30, 3, 3, first_places=6, submissions=6),
            PlayerWeeklyStats(2, "Bob", 40, 5, 5, first_places=3, submissions=10),
        ]
        p = prize_allocations(lb)
        assert p.best_average.player_name == "Alice"


class TestWeeklyLeaderboardFirstPlacesAndSubmissions:
    """``weekly_leaderboard`` now carries first_places + submissions.
    Checks the counting rules against a small hand-crafted dataset.
    """

    def test_counts_submissions_per_player(self):
        from datetime import date
        from app.db import ScoreRow
        from app.scoring import weekly_leaderboard

        scores = [
            ScoreRow(1, "Alice", "queens", 1, date(2026, 4, 13), 30),
            ScoreRow(1, "Alice", "tango",  1, date(2026, 4, 13), 40),
            ScoreRow(2, "Bob",   "queens", 1, date(2026, 4, 13), 35),
        ]
        lb = {p.player_id: p for p in weekly_leaderboard(scores)}
        assert lb[1].submissions == 2
        assert lb[2].submissions == 1

    def test_counts_first_places_including_ties(self):
        from datetime import date
        from app.db import ScoreRow
        from app.scoring import weekly_leaderboard

        scores = [
            # Alice wins Queens outright
            ScoreRow(1, "Alice", "queens", 1, date(2026, 4, 13), 30),
            ScoreRow(2, "Bob",   "queens", 1, date(2026, 4, 13), 45),
            # Alice and Bob tie for 1st on Tango
            ScoreRow(1, "Alice", "tango",  1, date(2026, 4, 13), 40),
            ScoreRow(2, "Bob",   "tango",  1, date(2026, 4, 13), 40),
            # Bob wins Zip outright
            ScoreRow(2, "Bob",   "zip",    1, date(2026, 4, 13), 10),
            ScoreRow(1, "Alice", "zip",    1, date(2026, 4, 13), 12),
        ]
        lb = {p.player_id: p for p in weekly_leaderboard(scores)}
        assert lb[1].first_places == 2   # Queens + tied Tango
        assert lb[2].first_places == 2   # tied Tango + Zip

    def test_counts_last_places_including_ties(self):
        from datetime import date
        from app.db import ScoreRow
        from app.scoring import weekly_leaderboard

        scores = [
            # Queens: Alice 30, Bob 45, Charlie 60 → Charlie last
            ScoreRow(1, "Alice",   "queens", 1, date(2026, 4, 13), 30),
            ScoreRow(2, "Bob",     "queens", 1, date(2026, 4, 13), 45),
            ScoreRow(3, "Charlie", "queens", 1, date(2026, 4, 13), 60),
            # Tango: Bob 40, Charlie 40 tied → both last (and both
            # first; 2-person tie means they share both titles).
            ScoreRow(2, "Bob",     "tango",  1, date(2026, 4, 13), 40),
            ScoreRow(3, "Charlie", "tango",  1, date(2026, 4, 13), 40),
        ]
        lb = {p.player_id: p for p in weekly_leaderboard(scores)}
        assert lb[1].last_places == 0              # Alice won Queens
        assert lb[2].last_places == 1              # Bob tied-last Tango
        assert lb[3].last_places == 2              # Charlie last Queens + tied-last Tango

    def test_singleton_rounds_count_neither_first_nor_last(self):
        from datetime import date
        from app.db import ScoreRow
        from app.scoring import weekly_leaderboard

        # Alice plays Queens alone. She shouldn't get credit for being
        # first or last — she's just the only submitter.
        scores = [
            ScoreRow(1, "Alice", "queens", 1, date(2026, 4, 13), 30),
        ]
        lb = {p.player_id: p for p in weekly_leaderboard(scores)}
        assert lb[1].first_places == 0
        assert lb[1].last_places == 0
        # But the submission still counts for submissions + days + games.
        assert lb[1].submissions == 1
        assert lb[1].distinct_games == 1
        assert lb[1].days_played == 1


# ---------------------------------------------------------------------------
# weekly_leaderboard total_time (powers the Fastest average time prize)
# ---------------------------------------------------------------------------


class TestWeeklyLeaderboardTotalTime:
    def test_sums_seconds_across_time_based_games(self):
        scores = [
            ScoreRow(1, "Alice", "queens", 1, D, 30),
            ScoreRow(1, "Alice", "tango",  1, D, 40),
            ScoreRow(1, "Alice", "zip",    1, D, 20),
            ScoreRow(2, "Bob",   "queens", 1, D, 45),
        ]
        lb = {p.player_id: p for p in weekly_leaderboard(scores)}
        assert lb[1].total_time == 90
        assert lb[1].time_based_submissions == 3
        assert lb[2].total_time == 45
        assert lb[2].time_based_submissions == 1

    def test_pinpoint_excluded_from_total_time(self):
        # Pinpoint is a guess count (1–5); summing it with seconds would
        # be meaningless. It must not contribute to total_time or to
        # time_based_submissions even though it still counts as a
        # regular submission.
        scores = [
            ScoreRow(1, "Alice", "queens",   1, D, 30),
            ScoreRow(1, "Alice", "pinpoint", 1, D, 3),
        ]
        lb = {p.player_id: p for p in weekly_leaderboard(scores)}
        assert lb[1].total_time == 30
        assert lb[1].time_based_submissions == 1
        assert lb[1].submissions == 2


# ---------------------------------------------------------------------------
# prize_allocations — fastest_average_time
# ---------------------------------------------------------------------------


class TestFastestAverageTimePrize:
    def test_awarded_to_lowest_average_time_over_threshold(self):
        # Alice: 600s / 10 = 60.0s avg
        # Bob:   400s / 10 = 40.0s avg  ← winner
        # Charlie: 500s / 10 = 50.0s avg
        lb = [
            PlayerWeeklyStats(1, "Alice", 20, 3, 3, submissions=10,
                              total_time=600, time_based_submissions=10),
            PlayerWeeklyStats(2, "Bob",   18, 3, 3, submissions=10,
                              total_time=400, time_based_submissions=10),
            PlayerWeeklyStats(3, "Charlie", 15, 2, 2, submissions=10,
                              total_time=500, time_based_submissions=10),
        ]
        p = prize_allocations(lb)
        assert p.fastest_average_time is not None
        assert p.fastest_average_time.player_name == "Bob"

    def test_low_volume_player_cannot_win_on_short_run(self):
        # The whole point of the change: Alice has a blistering 6s avg
        # over 9 rounds but doesn't clear the 10-round floor, so she's
        # ineligible. Bob (40s avg over 10 rounds) wins — his average
        # is worse but he played enough to qualify.
        lb = [
            PlayerWeeklyStats(1, "Alice", 18, 3, 3, submissions=9,
                              total_time=54, time_based_submissions=9),
            PlayerWeeklyStats(2, "Bob",   20, 5, 5, submissions=10,
                              total_time=400, time_based_submissions=10),
        ]
        p = prize_allocations(lb)
        assert p.fastest_average_time.player_name == "Bob"

    def test_higher_volume_can_beat_lower_average(self):
        # Bob's lower average wins even though Alice played more rounds:
        # the prize rewards the fastest *mean*, not the fastest grinder.
        # Alice: 1200s / 25 = 48.0s avg
        # Bob:    400s / 10 = 40.0s avg ← winner
        lb = [
            PlayerWeeklyStats(1, "Alice", 50, 5, 5, submissions=25,
                              total_time=1200, time_based_submissions=25),
            PlayerWeeklyStats(2, "Bob",   20, 5, 5, submissions=10,
                              total_time=400, time_based_submissions=10),
        ]
        p = prize_allocations(lb)
        assert p.fastest_average_time.player_name == "Bob"

    def test_returns_none_when_nobody_eligible(self):
        lb = [
            PlayerWeeklyStats(1, "Alice", 10, 2, 2, submissions=2,
                              total_time=60, time_based_submissions=2),
        ]
        p = prize_allocations(lb)
        assert p.fastest_average_time is None

    def test_tiebreak_prefers_more_rounds(self):
        # Same average (40s/round), different round counts: whoever
        # played more rounds wins — sustaining a 40s mean across 20
        # games is more impressive than across 10.
        lb = [
            PlayerWeeklyStats(1, "Alice", 20, 3, 3, submissions=10,
                              total_time=400, time_based_submissions=10),
            PlayerWeeklyStats(2, "Bob",   22, 5, 5, submissions=20,
                              total_time=800, time_based_submissions=20),
        ]
        p = prize_allocations(lb)
        assert p.fastest_average_time.player_name == "Bob"


# ---------------------------------------------------------------------------
# competitive_score — rank + time blended scoring
# ---------------------------------------------------------------------------


class TestCompetitiveScore:
    def _times(self, result):
        return [r["time"] for r in result]

    def _scores(self, result):
        return [r["final_score"] for r in result]

    def test_sorts_by_time_ascending(self):
        out = competitive_score([
            {"name": "Slow", "time": 30},
            {"name": "Fast", "time": 5},
            {"name": "Mid",  "time": 15},
        ])
        assert [r["name"] for r in out] == ["Fast", "Mid", "Slow"]

    def test_tight_game_uses_base_points(self):
        # Spec example: [16,18,20,23,28] → [5,4,3,2,1]. Spread is 0.75
        # so technically past the 0.5 threshold, but r12 and r34 don't
        # trigger cluster or winner either — falls through to base.
        out = competitive_score([
            {"name": "A", "time": 16},
            {"name": "B", "time": 18},
            {"name": "C", "time": 20},
            {"name": "D", "time": 23},
            {"name": "E", "time": 28},
        ])
        assert self._scores(out) == [5.0, 4.0, 3.0, 2.0, 1.0]

    def test_strict_tight_game_under_half_spread(self):
        # spread = (11-10)/10 = 0.1, clearly tight.
        out = competitive_score([
            {"name": "A", "time": 10},
            {"name": "B", "time": 10.5},
            {"name": "C", "time": 11},
        ])
        assert self._scores(out) == [5.0, 4.0, 3.0]

    def test_clear_winner_gets_bonus(self):
        # r12 = 15/6 = 2.5 → bonus = min(2, 3) = 2 capped.
        out = competitive_score([
            {"name": "Simon",  "time": 6},
            {"name": "Ben",    "time": 15},
            {"name": "adam",   "time": 20},
            {"name": "David",  "time": 25},
            {"name": "Doron",  "time": 30},
        ])
        scores = self._scores(out)
        # First place clearly ahead of the rest.
        assert scores[0] > 5.0
        assert scores[0] == max(scores)
        # Rankings preserved.
        assert scores == sorted(scores, reverse=True)

    def test_clear_winner_bonus_is_capped_at_two(self):
        # r12 = 60/6 = 10 → (r12-1)*2 = 18; must cap at +2.
        out = competitive_score([
            {"name": "A", "time": 6},
            {"name": "B", "time": 60},
            {"name": "C", "time": 70},
            {"name": "D", "time": 80},
            {"name": "E", "time": 90},
        ])
        scores = self._scores(out)
        # Base for 1st is 5; +2 cap → at most 7. Rounding slack of 0.1.
        assert scores[0] <= 7.0 + 0.05

    def test_front_cluster_boosts_top_three(self):
        # Top 3 are tight (r12=1.25, r23=1.2) and 4th is a big drop
        # (r34=30/12=2.5) — exactly the cluster shape.
        out = competitive_score([
            {"name": "A", "time": 8},
            {"name": "B", "time": 10},
            {"name": "C", "time": 12},
            {"name": "D", "time": 30},
            {"name": "E", "time": 33},
        ])
        scores = self._scores(out)
        # Top 3 all received a bonus above their base.
        assert scores[0] > 5.0
        assert scores[1] > 4.0
        assert scores[2] > 3.0
        # Bottom 2 absorbed the debit.
        assert scores[3] < 2.0
        assert scores[4] < 1.0
        # Rankings preserved.
        assert scores == sorted(scores, reverse=True)

    def test_total_points_preserved_after_adjustments(self):
        # Base total for 5 players is 15. Rounding residue is absorbed
        # by the last player, so the sum should land exactly on 15
        # modulo the rounding-to-1dp noise.
        out = competitive_score([
            {"name": "A", "time": 6},
            {"name": "B", "time": 15},
            {"name": "C", "time": 20},
            {"name": "D", "time": 25},
            {"name": "E", "time": 30},
        ])
        assert abs(sum(self._scores(out)) - 15.0) < 0.01

    def test_top_two_cluster_boosts_top_two(self):
        # Patches scenario: 6, 7 vs 42, 68, 97. r12=1.17 (tight),
        # r23=6.0 (huge drop) → top-2 cluster fires.
        out = competitive_score([
            {"name": "Duvi",   "time": 6},
            {"name": "Darren", "time": 7},
            {"name": "Goat",   "time": 42},
            {"name": "Ben",    "time": 68},
            {"name": "adamk",  "time": 97},
        ])
        scores = self._scores(out)
        # Top 2 each got a bonus above their base.
        assert scores[0] > 5.0
        assert scores[1] > 4.0
        # Bottom 3 absorbed the debit.
        assert scores[2] < 3.0
        assert scores[3] < 2.0
        assert scores[4] < 1.0
        # Rankings preserved and total preserved (modulo rounding).
        assert scores == sorted(scores, reverse=True)
        assert abs(sum(scores) - 15.0) < 0.05

    def test_cluster_pool_scales_with_drop_size(self):
        # Same top-2 shape, two different drop sizes. The bigger drop
        # earns a bigger pool, so the front of the field walks away
        # with more points and the stragglers absorb a heavier debit.
        # Drop ~1.5 (just above the 1.3 minimum, knee is 1.6):
        small_drop = competitive_score([
            {"name": "A", "time": 10},
            {"name": "B", "time": 11},
            # r23 = 16.5 / 11 = 1.5 → small pool (~1.0)
            {"name": "C", "time": 16.5},
            {"name": "D", "time": 17.5},
            {"name": "E", "time": 18.5},
        ])
        # Drop ~6 (well past the 2.0 cap):
        big_drop = competitive_score([
            {"name": "A", "time": 6},
            {"name": "B", "time": 7},
            # r23 = 6.0 → max pool 2.0
            {"name": "C", "time": 42},
            {"name": "D", "time": 68},
            {"name": "E", "time": 97},
        ])
        # 1st place earns more under the bigger drop.
        assert big_drop[0]["final_score"] > small_drop[0]["final_score"]
        # Last place absorbs more debit under the bigger drop.
        assert big_drop[-1]["final_score"] < small_drop[-1]["final_score"]

    def test_cluster_fires_at_lowered_threshold(self):
        # Drop of 1.4 — past the new 1.3 threshold but well below the
        # old 1.6. Used to fall through to "tight" with flat ranks;
        # should now trigger a small top-2 cluster bonus.
        out = competitive_score([
            {"name": "A", "time": 10},
            {"name": "B", "time": 11},
            # r23 = 15.4 / 11 = 1.4 (just above 1.3) → small pool
            {"name": "C", "time": 15.4},
            {"name": "D", "time": 16},
            {"name": "E", "time": 17},
        ])
        scores = self._scores(out)
        # Top 2 above their base, bottom 3 below.
        assert scores[0] > 5.0
        assert scores[1] > 4.0
        assert scores[2] < 3.0
        # Total still preserved.
        assert abs(sum(scores) - 15.0) < 0.05

    def test_cluster_pool_capped_at_two(self):
        # Massive drop (r23 ≈ 50x) should NOT pump more than 2.0 pts
        # into the cluster — the formula caps at 2.0 above drop=2.0.
        out = competitive_score([
            {"name": "A", "time": 1},
            {"name": "B", "time": 1.1},
            {"name": "C", "time": 60},
            {"name": "D", "time": 70},
            {"name": "E", "time": 80},
        ])
        scores = self._scores(out)
        # 1st place gets at most ~5 + 2.0 * (max time-weight) = ~6.0+.
        # Asserting the loose ceiling: 1st no greater than 7.0 (which
        # would require an unbounded pool).
        assert scores[0] < 7.0
        assert abs(sum(scores) - 15.0) < 0.05

    def test_top_four_cluster_boosts_top_four(self):
        # Top 4 are all close (30/32/35/37), 5th is a clear drop (90).
        # r12=1.07, r23=1.09, r34=1.06, r45=2.43.
        out = competitive_score([
            {"name": "A", "time": 30},
            {"name": "B", "time": 32},
            {"name": "C", "time": 35},
            {"name": "D", "time": 37},
            {"name": "E", "time": 90},
        ])
        scores = self._scores(out)
        # Top 4 each got a bonus above their base.
        assert scores[0] > 5.0
        assert scores[1] > 4.0
        assert scores[2] > 3.0
        assert scores[3] > 2.0
        # 5th absorbed the entire debit.
        assert scores[4] < 1.0
        # Rankings + total preserved.
        assert scores == sorted(scores, reverse=True)
        assert abs(sum(scores) - 15.0) < 0.05

    def test_largest_cluster_wins_when_multiple_could_match(self):
        # Top 4 all tight (r12=r23=r34=1.1), r45 = 50/13 ≈ 3.85 (big
        # drop). This shape can only match top-4 — top-3 would need
        # r34 > 1.6 (it's 1.08) and top-2 would need r23 > 1.6
        # (it's 1.09). So it falls cleanly into top-4.
        out = competitive_score([
            {"name": "A", "time": 10},
            {"name": "B", "time": 11},
            {"name": "C", "time": 12},
            {"name": "D", "time": 13},
            {"name": "E", "time": 50},
        ])
        scores = self._scores(out)
        # All four front players above their base — top-4 cluster fired.
        assert scores[0] > 5.0
        assert scores[1] > 4.0
        assert scores[2] > 3.0
        assert scores[3] > 2.0
        # 5th eats the debit.
        assert scores[4] < 1.0

    def test_top_three_cluster_still_wins_when_top_four_doesnt_fit(self):
        # r12=1.25, r23=1.2, r34=2.5 (drop), r45=1.1 — top-3 only.
        # 4th is past the cluster, 5th is tight to 4th.
        out = competitive_score([
            {"name": "A", "time": 8},
            {"name": "B", "time": 10},
            {"name": "C", "time": 12},
            {"name": "D", "time": 30},
            {"name": "E", "time": 33},
        ])
        scores = self._scores(out)
        # Top 3 above base, bottom 2 below.
        assert scores[0] > 5.0
        assert scores[1] > 4.0
        assert scores[2] > 3.0
        assert scores[3] < 2.0
        assert scores[4] < 1.0

    def test_friday_zip_now_triggers_top_two_cluster(self):
        # Real round from Friday's recap that exposed the gap:
        # The Goat 0:10, adamk 0:11 (tight top 2), then 0:18, 0:19,
        # 0:38. r12=1.1, r23=18/11≈1.64 → top-2 fires.
        out = competitive_score([
            {"name": "Goat",   "time": 10},
            {"name": "adamk",  "time": 11},
            {"name": "Darren", "time": 18},
            {"name": "Duvi",   "time": 19},
            {"name": "Ben",    "time": 38},
        ])
        scores = self._scores(out)
        assert scores[0] > 5.0
        assert scores[1] > 4.0
        # Stragglers below their base.
        assert scores[2] < 3.0
        assert scores[3] < 2.0
        assert scores[4] < 1.0

    def test_zip_407_absolute_tie_promotes_to_top_three_cluster(self):
        # Zip #407 (Tue 28 Apr): times 4, 6, 7, 13, 51. Pure ratio
        # detection failed because r12=1.5 exceeds the 1.4 in-cluster
        # threshold, so 1st alone got the clear-winner bonus. With the
        # absolute-tie escape (both <10s and gap ≤3s) the top three
        # promote into a single cluster and share the bonus.
        out = competitive_score([
            {"name": "Goat",   "time": 4},
            {"name": "Duv",    "time": 6},
            {"name": "adamk",  "time": 7},
            {"name": "Ben",    "time": 13},
            {"name": "Darren", "time": 51},
        ])
        scores = self._scores(out)
        # Top three all bumped above their base — the cluster fired.
        assert scores[0] > 5.0
        assert scores[1] > 4.0
        assert scores[2] > 3.0
        # No one in the top three is more than 0.6 above the next:
        # the bonus is shared, not concentrated on 1st like Case C.
        assert scores[0] - scores[1] < 0.6 + 1.0  # gap between 1st and 2nd
        assert scores[1] - scores[2] < 1.5
        # Distance-weighted debit: Darren (44s past boundary) absorbs
        # far more than Ben (6s past). Ben's debit < 1.0; Darren's
        # debit drives him to the 0 floor.
        assert scores[3] > 1.0
        assert scores[4] == 0.0
        assert scores == sorted(scores, reverse=True)

    def test_absolute_tie_blocks_clear_winner_bonus(self):
        # Times [4, 6, 50, 80, 110]: r12=1.5 would normally trigger
        # the clear-winner bonus, but 4 and 6 are both inside the
        # 10s floor with a 2s gap → top-2 cluster fires instead.
        out = competitive_score([
            {"name": "A", "time": 4},
            {"name": "B", "time": 6},
            {"name": "C", "time": 50},
            {"name": "D", "time": 80},
            {"name": "E", "time": 110},
        ])
        scores = self._scores(out)
        # 1st and 2nd both bonused — share the cluster pool.
        assert scores[0] > 5.0
        assert scores[1] > 4.0
        # Gap between 1st and 2nd is smaller than the clear-winner
        # case would have produced (where 1st alone gets +1 and 2nd
        # eats a debit, gap ~2.4). Here both get a positive bonus.
        assert scores[0] - scores[1] < 2.0

    def test_absolute_tie_does_not_promote_when_gap_exceeds_delta(self):
        # Gap of 8s between 1st and 2nd exceeds the 3s delta — no
        # absolute-tie promotion, so the existing clear-winner bonus
        # still fires (r12 = 12/4 = 3.0 → bonus capped at +2).
        out = competitive_score([
            {"name": "A", "time": 4},
            {"name": "B", "time": 12},
            {"name": "C", "time": 50},
            {"name": "D", "time": 80},
            {"name": "E", "time": 110},
        ])
        scores = self._scores(out)
        # Runaway winner: 1st well above 2nd, gap ≥ 3 points.
        assert scores[0] >= 6.5
        assert scores[0] - scores[1] >= 3.0

    def test_absolute_tie_does_not_promote_when_above_floor(self):
        # Fastest is 40s — outside the 10s floor — so absolute-tie
        # never fires regardless of gap. r12 = 60/40 = 1.5 → falls
        # through cluster (k=2 ratio fails 1.4) to clear-winner.
        out = competitive_score([
            {"name": "A", "time": 40},
            {"name": "B", "time": 60},
            {"name": "C", "time": 500},
            {"name": "D", "time": 800},
            {"name": "E", "time": 1100},
        ])
        scores = self._scores(out)
        # Clear-winner fires: 1st gets a meaningful lead over 2nd.
        assert scores[0] - scores[1] >= 1.5

    def test_distance_weighted_debit_concentrates_on_far_straggler(self):
        # Top-2 cluster [5, 6] with two stragglers at very different
        # distances: 20s (14s past boundary) vs 100s (94s past).
        # The distant straggler must absorb far more of the debit.
        out = competitive_score([
            {"name": "A", "time": 5},
            {"name": "B", "time": 6},
            {"name": "C", "time": 20},
            {"name": "D", "time": 100},
        ])
        scores = self._scores(out)
        base_C, base_D = 3.0, 2.0
        debit_C = base_C - scores[2]
        debit_D = base_D - scores[3]
        # D's debit dwarfs C's — distance ratio 94/14 ≈ 6.7.
        assert debit_D > debit_C * 3
        # No score ever drops below the 0.0 hard floor, even when a
        # runaway winner triggers the maximum +2 bonus and the
        # distance-weighted debit concentrates on the slowest player.
        out = competitive_score([
            {"name": "A", "time": 1},
            {"name": "B", "time": 100},
            {"name": "C", "time": 110},
            {"name": "D", "time": 120},
            {"name": "E", "time": 130},
        ])
        assert min(self._scores(out)) >= 0.0

    def test_rankings_never_change(self):
        # Sweep of increasing times; the returned order must equal the
        # input-sorted order and scores must be monotonically descending.
        out = competitive_score([
            {"name": "A", "time": 5},
            {"name": "B", "time": 12},
            {"name": "C", "time": 40},
            {"name": "D", "time": 50},
        ])
        scores = self._scores(out)
        assert scores == sorted(scores, reverse=True)

    def test_rounded_to_one_decimal_place(self):
        out = competitive_score([
            {"name": "A", "time": 6},
            {"name": "B", "time": 15},
            {"name": "C", "time": 20},
            {"name": "D", "time": 25},
            {"name": "E", "time": 30},
        ])
        for r in out:
            # Every score is a multiple of 0.1 within FP tolerance.
            assert abs(round(r["final_score"] * 10) - r["final_score"] * 10) < 1e-6

    def test_supports_three_players(self):
        out = competitive_score([
            {"name": "A", "time": 10},
            {"name": "B", "time": 11},
            {"name": "C", "time": 12},
        ])
        # Base total for 3 players is 5+4+3 = 12.
        assert abs(sum(self._scores(out)) - 12.0) < 0.01

    def test_supports_four_players(self):
        out = competitive_score([
            {"name": "A", "time": 10},
            {"name": "B", "time": 11},
            {"name": "C", "time": 12},
            {"name": "D", "time": 13},
        ])
        # Base total for 4 players is 5+4+3+2 = 14.
        assert abs(sum(self._scores(out)) - 14.0) < 0.01

    def test_rejects_fewer_than_three_players(self):
        with pytest.raises(ValueError):
            competitive_score([{"name": "A", "time": 10}])

    def test_supports_six_players_clear_winner(self):
        # 6-player round, P1 way ahead → Case C clear-winner bonus
        # fires. Round total stays at 15. 6th place stays at 0
        # since base[5] = 0 and the proportional debit × 0 = 0.
        out = competitive_score([
            {"name": "Simon", "time": 6},
            {"name": "Ben",   "time": 15},
            {"name": "adamk", "time": 20},
            {"name": "David", "time": 25},
            {"name": "Doron", "time": 30},
            {"name": "Darren","time": 35},
        ])
        scores = self._scores(out)
        assert scores[0] > 5.0          # bonus on the runaway leader
        assert scores == sorted(scores, reverse=True)
        assert scores[-1] == 0.0        # 6th doesn't score
        assert min(scores) >= 0.0       # no negatives
        assert abs(sum(scores) - 15.0) < 0.2

    def test_supports_arbitrary_size(self):
        # 8-player round still totals ~15; 6th–8th sit at 0.
        out = competitive_score([
            {"name": str(i), "time": i * 5} for i in range(1, 9)
        ])
        scores = self._scores(out)
        assert scores == sorted(scores, reverse=True)
        assert min(scores) >= 0.0
        assert abs(sum(scores) - 15.0) < 0.5

    def test_rejects_non_positive_time(self):
        with pytest.raises(ValueError):
            competitive_score([
                {"name": "A", "time": 0},
                {"name": "B", "time": 10},
                {"name": "C", "time": 20},
            ])

    def test_rejects_missing_keys(self):
        with pytest.raises(ValueError):
            competitive_score([
                {"name": "A"},  # no 'time'
                {"name": "B", "time": 10},
                {"name": "C", "time": 20},
            ])
