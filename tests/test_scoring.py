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
        assert prize_allocations([]) == Prizes(None, None, None, None)

    def test_distinct_winners(self):
        lb = [
            PlayerWeeklyStats(1, "Alice", 25, 3, 2),
            PlayerWeeklyStats(2, "Bob", 20, 5, 3),
            PlayerWeeklyStats(3, "Charlie", 15, 2, 2),
            PlayerWeeklyStats(4, "Dee", 3, 1, 1),
        ]
        p = prize_allocations(lb)
        assert p.champion.player_name == "Alice"
        assert p.all_rounder.player_name == "Bob"
        assert p.streak_king.player_name == "Bob"
        assert p.wooden_spoon.player_name == "Dee"

    def test_all_rounder_tiebreaks_on_points(self):
        lb = [
            PlayerWeeklyStats(1, "Alice", 10, 3, 2),
            PlayerWeeklyStats(2, "Bob", 15, 3, 2),
        ]
        assert prize_allocations(lb).all_rounder.player_name == "Bob"
