"""Unit tests for ``app.scoring``."""

from __future__ import annotations

from datetime import date

import pytest

from app.db import ScoreRow
from app.scoring import (
    PlayerWeeklyStats,
    Prizes,
    assign_daily_points,
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
# assign_daily_points
# ---------------------------------------------------------------------------


class TestAssignDailyPoints:
    def test_empty_returns_empty_dict(self):
        assert assign_daily_points([]) == {}

    def test_single_player_gets_5(self):
        assert assign_daily_points(
            [_row(1, "Alice", "queens", 714, 10)]
        ) == {1: 5}

    def test_straight_ranking(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(2, "Bob", "queens", 714, 20),
            _row(3, "Charlie", "queens", 714, 30),
            _row(4, "Dee", "queens", 714, 40),
        ]
        assert assign_daily_points(scores) == {1: 5, 2: 3, 3: 1, 4: 0}

    def test_tied_first_both_get_5_and_next_is_third(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(2, "Bob", "queens", 714, 10),
            _row(3, "Charlie", "queens", 714, 20),
            _row(4, "Dee", "queens", 714, 30),
        ]
        # Alice & Bob tied 1st (5 each); Charlie = 3rd (1 pt); Dee = 4th (0)
        assert assign_daily_points(scores) == {1: 5, 2: 5, 3: 1, 4: 0}

    def test_tied_second_both_get_3_next_is_fourth(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(2, "Bob", "queens", 714, 20),
            _row(3, "Charlie", "queens", 714, 20),
            _row(4, "Dee", "queens", 714, 30),
        ]
        # Alice 1st (5); Bob & Charlie tied 2nd (3 each); Dee = 4th (0)
        assert assign_daily_points(scores) == {1: 5, 2: 3, 3: 3, 4: 0}

    def test_three_way_tie_at_top(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(2, "Bob", "queens", 714, 10),
            _row(3, "Charlie", "queens", 714, 10),
            _row(4, "Dee", "queens", 714, 30),
        ]
        # Three-way tie for 1st (all 5); Dee = 4th (0), NOT 2nd
        assert assign_daily_points(scores) == {1: 5, 2: 5, 3: 5, 4: 0}

    def test_pinpoint_lower_guess_count_wins(self):
        scores = [
            _row(1, "Alice", "pinpoint", 714, 3),
            _row(2, "Bob", "pinpoint", 714, 2),
            _row(3, "Charlie", "pinpoint", 714, 5),
        ]
        assert assign_daily_points(scores) == {1: 3, 2: 5, 3: 1}

    def test_fourth_place_and_beyond_get_zero(self):
        scores = [
            _row(i, f"P{i}", "queens", 714, i * 10)
            for i in range(1, 8)
        ]
        result = assign_daily_points(scores)
        assert result[1] == 5
        assert result[2] == 3
        assert result[3] == 1
        for i in range(4, 8):
            assert result[i] == 0


# ---------------------------------------------------------------------------
# weekly_leaderboard
# ---------------------------------------------------------------------------


class TestWeeklyLeaderboard:
    def test_empty_returns_empty(self):
        assert weekly_leaderboard([]) == []

    def test_single_game_single_day(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(2, "Bob", "queens", 714, 20),
        ]
        lb = weekly_leaderboard(scores)
        assert lb == [
            PlayerWeeklyStats(1, "Alice", 5, 1, 1),
            PlayerWeeklyStats(2, "Bob", 3, 1, 1),
        ]

    def test_aggregates_across_games(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),  # 5
            _row(2, "Bob", "queens", 714, 20),  # 3
            _row(1, "Alice", "tango", 554, 30),  # 3
            _row(2, "Bob", "tango", 554, 20),  # 5
        ]
        lb = weekly_leaderboard(scores)
        # Alice & Bob both 8; Alice first by player_id tiebreak
        assert lb[0].player_id == 1
        assert lb[0].total_points == 8
        assert lb[0].distinct_games == 2
        assert lb[0].days_played == 1
        assert lb[1].player_id == 2
        assert lb[1].total_points == 8

    def test_counts_days_played_as_distinct_puzzle_dates(self):
        d1 = date(2026, 4, 13)
        d2 = date(2026, 4, 14)
        scores = [
            _row(1, "Alice", "queens", 713, 10, puzzle_date=d1),
            _row(1, "Alice", "queens", 714, 10, puzzle_date=d2),
            _row(2, "Bob", "queens", 714, 20, puzzle_date=d2),
        ]
        lb = weekly_leaderboard(scores)
        alice = next(p for p in lb if p.player_id == 1)
        bob = next(p for p in lb if p.player_id == 2)
        assert alice.days_played == 2
        assert bob.days_played == 1

    def test_zero_points_player_still_listed(self):
        # Alice wins, Charlie plays but is 4th and scores 0 pts
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(2, "Bob", "queens", 714, 20),
            _row(3, "Charlie", "queens", 714, 30),
            _row(4, "Dee", "queens", 714, 40),
        ]
        lb = weekly_leaderboard(scores)
        assert len(lb) == 4
        dee = next(p for p in lb if p.player_id == 4)
        assert dee.total_points == 0
        assert dee.distinct_games == 1

    def test_sort_order_points_desc_stable_by_player_id(self):
        scores = [
            _row(3, "Charlie", "queens", 714, 30),
            _row(1, "Alice", "queens", 714, 20),
            _row(2, "Bob", "queens", 714, 10),
        ]
        lb = weekly_leaderboard(scores)
        assert [p.player_name for p in lb] == ["Bob", "Alice", "Charlie"]

    def test_midnight_spanning_shared_puzzle(self):
        """Two players' Queens #714 submissions straddle midnight: one
        row has puzzle_date=Monday, the other Tuesday, but both are the
        same ``(game, puzzle_no)`` round so they compete head-to-head."""
        monday = date(2026, 4, 13)
        tuesday = date(2026, 4, 14)
        scores = [
            _row(1, "Alice", "queens", 714, 10, puzzle_date=monday),
            _row(2, "Bob", "queens", 714, 20, puzzle_date=tuesday),
        ]
        lb = weekly_leaderboard(scores)
        alice = next(p for p in lb if p.player_id == 1)
        bob = next(p for p in lb if p.player_id == 2)
        assert alice.total_points == 5  # 1st in Queens #714 round
        assert bob.total_points == 3  # 2nd in the same round


# ---------------------------------------------------------------------------
# prize_allocations
# ---------------------------------------------------------------------------


class TestPrizeAllocations:
    def test_empty_leaderboard(self):
        assert prize_allocations([]) == Prizes(None, None, None, None)

    def test_distinct_winners_for_each_prize(self):
        lb = [
            PlayerWeeklyStats(1, "Alice", 25, 3, 2),  # champion (most pts)
            PlayerWeeklyStats(2, "Bob", 20, 5, 3),  # all-rounder + streak
            PlayerWeeklyStats(3, "Charlie", 15, 2, 2),
            PlayerWeeklyStats(4, "Dee", 3, 1, 1),  # wooden spoon
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

    def test_streak_king_tiebreaks_on_points(self):
        lb = [
            PlayerWeeklyStats(1, "Alice", 5, 2, 3),
            PlayerWeeklyStats(2, "Bob", 15, 3, 3),
        ]
        assert prize_allocations(lb).streak_king.player_name == "Bob"

    def test_champion_tie_resolved_by_smallest_player_id(self):
        lb = [
            PlayerWeeklyStats(2, "Bob", 10, 1, 1),
            PlayerWeeklyStats(1, "Alice", 10, 1, 1),
        ]
        assert prize_allocations(lb).champion.player_name == "Alice"

    def test_wooden_spoon_tie_resolved_by_smallest_player_id(self):
        lb = [
            PlayerWeeklyStats(2, "Bob", 0, 0, 0),
            PlayerWeeklyStats(1, "Alice", 0, 0, 0),
        ]
        assert prize_allocations(lb).wooden_spoon.player_name == "Alice"
