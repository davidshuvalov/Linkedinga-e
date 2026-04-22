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
    def test_solo_positions(self):
        assert _tied_points(1, 1) == 5
        assert _tied_points(2, 1) == 4
        assert _tied_points(3, 1) == 3
        assert _tied_points(4, 1) == 2
        assert _tied_points(5, 1) == 1
        assert _tied_points(6, 1) == 0

    def test_tied_2nd_3rd_averages_to_4(self):
        # (4+3)/2 = 3.5 → ceil → 4
        assert _tied_points(2, 2) == 4

    def test_three_way_tie_1st_averages_to_4(self):
        # (5+4+3)/3 = 4.0 → ceil → 4
        assert _tied_points(1, 3) == 4

    def test_tied_4th_5th_averages_to_2(self):
        # (2+1)/2 = 1.5 → ceil → 2
        assert _tied_points(4, 2) == 2

    def test_tied_5th_6th_averages_to_1(self):
        # (1+0)/2 = 0.5 → ceil → 1
        assert _tied_points(5, 2) == 1

    def test_tied_1st_2nd_averages_to_5(self):
        # (5+4)/2 = 4.5 → ceil → 5
        assert _tied_points(1, 2) == 5

    def test_all_tied_beyond_5th(self):
        # (0+0)/2 = 0 → ceil → 0
        assert _tied_points(6, 2) == 0

    def test_three_way_tie_4th(self):
        # (2+1+0)/3 = 1.0 → ceil → 1
        assert _tied_points(4, 3) == 1


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

    def test_straight_ranking_5_players(self):
        scores = [_row(i, f"P{i}", "queens", 714, i * 10) for i in range(1, 6)]
        assert assign_daily_points(scores) == {
            1: 5, 2: 4, 3: 3, 4: 2, 5: 1,
        }

    def test_sixth_and_beyond_get_zero(self):
        scores = [_row(i, f"P{i}", "queens", 714, i * 10) for i in range(1, 8)]
        r = assign_daily_points(scores)
        assert r[6] == 0
        assert r[7] == 0

    def test_tied_2nd_3rd_both_get_4(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(2, "Bob", "queens", 714, 20),    # tied 2nd
            _row(3, "Charlie", "queens", 714, 20),  # tied 2nd
            _row(4, "Dee", "queens", 714, 30),
        ]
        r = assign_daily_points(scores)
        assert r == {1: 5, 2: 4, 3: 4, 4: 2}

    def test_three_way_tie_at_top_all_get_4(self):
        scores = [
            _row(1, "A", "queens", 714, 10),
            _row(2, "B", "queens", 714, 10),
            _row(3, "C", "queens", 714, 10),
            _row(4, "D", "queens", 714, 30),
        ]
        r = assign_daily_points(scores)
        # (5+4+3)/3 = 4; D is 4th = 2
        assert r == {1: 4, 2: 4, 3: 4, 4: 2}

    def test_tied_1st_2nd_both_get_5(self):
        scores = [
            _row(1, "A", "queens", 714, 10),
            _row(2, "B", "queens", 714, 10),
            _row(3, "C", "queens", 714, 30),
        ]
        r = assign_daily_points(scores)
        # (5+4)/2 = 4.5 → 5; C is 3rd = 3
        assert r == {1: 5, 2: 5, 3: 3}

    def test_tied_4th_5th_both_get_2(self):
        scores = [
            _row(1, "A", "queens", 714, 10),
            _row(2, "B", "queens", 714, 20),
            _row(3, "C", "queens", 714, 30),
            _row(4, "D", "queens", 714, 40),  # tied 4th
            _row(5, "E", "queens", 714, 40),  # tied 4th
        ]
        r = assign_daily_points(scores)
        assert r == {1: 5, 2: 4, 3: 3, 4: 2, 5: 2}

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
