"""Unit tests for ``app.scheduler`` — daily recap and weekly wrap formatting."""

from __future__ import annotations

from datetime import date

from app.db import ScoreRow
from app.scheduler import daily_recap, weekly_wrap

TUE = date(2026, 4, 14)
MON = date(2026, 4, 13)
SUN = date(2026, 4, 19)


def _row(
    pid: int,
    name: str,
    game: str,
    puzzle_no: int,
    raw: int,
    puzzle_date: date = TUE,
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
# daily_recap
# ---------------------------------------------------------------------------


class TestDailyRecap:
    def test_no_scores(self):
        out = daily_recap(TUE, [])
        assert "Daily recap" in out
        assert "Tue 14 Apr 2026" in out
        assert "No scores yet" in out

    def test_header_and_sections(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(2, "Bob", "queens", 714, 20),
            _row(3, "Charlie", "queens", 714, 30),
        ]
        out = daily_recap(TUE, scores)
        assert "Daily recap — Tue 14 Apr 2026" in out
        assert "Queens #714" in out
        assert "Alice" in out and "0:10" in out
        assert "(5 pts)" in out
        assert "(3 pts)" in out
        assert "(1 pt)" in out  # singular
        assert "Day totals:" in out

    def test_pinpoint_renders_as_guesses(self):
        scores = [
            _row(1, "Alice", "pinpoint", 714, 3),
            _row(2, "Bob", "pinpoint", 714, 1),
        ]
        out = daily_recap(TUE, scores)
        assert "Pinpoint #714" in out
        assert "1 guess" in out
        assert "3 guesses" in out

    def test_mini_sudoku_keeps_space_in_display(self):
        scores = [_row(1, "Alice", "mini_sudoku", 246, 76)]
        out = daily_recap(TUE, scores)
        assert "Mini Sudoku #246" in out
        assert "1:16" in out

    def test_scores_from_other_days_are_ignored(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10, puzzle_date=TUE),
            _row(2, "Bob", "queens", 713, 10, puzzle_date=MON),
        ]
        out = daily_recap(TUE, scores)
        assert "Alice" in out
        assert "Bob" not in out
        # Monday's Queens puzzle number should not leak in
        assert "#713" not in out

    def test_day_totals_sum_across_games_with_ties_shared(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),  # 5
            _row(2, "Bob", "queens", 714, 10),  # 5 (tied)
            _row(1, "Alice", "tango", 554, 30),  # 5
            _row(2, "Bob", "tango", 554, 45),  # 3
        ]
        out = daily_recap(TUE, scores)
        # Alice: 5 + 5 = 10; Bob: 5 + 3 = 8
        assert "Alice: 10 pts" in out
        assert "Bob: 8 pts" in out

    def test_game_order_is_stable(self):
        scores = [
            _row(1, "Alice", "mini_sudoku", 246, 76),
            _row(1, "Alice", "queens", 714, 10),
            _row(1, "Alice", "tango", 554, 35),
        ]
        out = daily_recap(TUE, scores)
        queens_idx = out.index("Queens #")
        tango_idx = out.index("Tango #")
        sudoku_idx = out.index("Mini Sudoku #")
        assert queens_idx < tango_idx < sudoku_idx


# ---------------------------------------------------------------------------
# weekly_wrap
# ---------------------------------------------------------------------------


class TestWeeklyWrap:
    def test_no_scores(self):
        out = weekly_wrap(MON, SUN, [])
        assert "Weekly wrap" in out
        assert "No scores this week" in out

    def test_header_spans_week_bounds(self):
        scores = [_row(1, "Alice", "queens", 714, 10, puzzle_date=TUE)]
        out = weekly_wrap(MON, SUN, scores)
        assert "Mon 13 Apr" in out
        assert "Sun 19 Apr 2026" in out

    def test_leaderboard_and_prizes_sections(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10, puzzle_date=TUE),
            _row(2, "Bob", "queens", 714, 20, puzzle_date=TUE),
            _row(1, "Alice", "tango", 554, 20, puzzle_date=TUE),
            _row(2, "Bob", "tango", 554, 30, puzzle_date=TUE),
        ]
        out = weekly_wrap(MON, SUN, scores)
        assert "Leaderboard:" in out
        assert "Prizes:" in out
        assert "Champion" in out
        assert "All-rounder" in out
        assert "Streak king" in out
        assert "Wooden spoon" in out
        # Alice wins: 5 + 5 = 10; Bob: 3 + 3 = 6
        alice_idx = out.index("Alice")
        bob_idx = out.index("Bob")
        assert alice_idx < bob_idx  # leaderboard sorted desc
        assert "Champion: Alice" in out

    def test_leaderboard_includes_games_and_days(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10, puzzle_date=TUE),
            _row(1, "Alice", "tango", 554, 20, puzzle_date=MON),
        ]
        out = weekly_wrap(MON, SUN, scores)
        # Alice: 2 games, 2 days — singular/plural already tested elsewhere
        assert "2 games" in out
        assert "2 days" in out

    def test_scores_outside_week_filtered(self):
        prev_sun = date(2026, 4, 12)
        scores = [
            _row(1, "Alice", "queens", 713, 10, puzzle_date=prev_sun),
            _row(2, "Bob", "queens", 714, 20, puzzle_date=TUE),
        ]
        out = weekly_wrap(MON, SUN, scores)
        assert "Bob" in out
        assert "Alice" not in out

    def test_singular_day_and_game_wording(self):
        scores = [_row(1, "Alice", "queens", 714, 10, puzzle_date=TUE)]
        out = weekly_wrap(MON, SUN, scores)
        assert "1 game" in out and "1 games" not in out
        assert "1 day" in out and "1 days" not in out
