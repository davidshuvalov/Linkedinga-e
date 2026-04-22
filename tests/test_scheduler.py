"""Unit tests for ``app.scheduler`` — daily recap and weekly wrap formatting."""

from __future__ import annotations

from datetime import date

from app.db import ScoreRow
from app.scheduler import daily_recap, weekly_wrap

TUE = date(2026, 4, 14)
MON = date(2026, 4, 13)
SUN = date(2026, 4, 19)

ENABLED = frozenset({"queens", "tango", "crossclimb", "zip", "patches", "mini_sudoku"})


def _row(
    pid: int,
    name: str,
    game: str,
    puzzle_no: int,
    raw: int,
    puzzle_date: date = TUE,
) -> ScoreRow:
    return ScoreRow(pid, name, game, puzzle_no, puzzle_date, raw)


# ---------------------------------------------------------------------------
# daily_recap
# ---------------------------------------------------------------------------


class TestDailyRecap:
    def test_no_scores(self):
        out = daily_recap(TUE, [], ENABLED)
        assert "No scores yet" in out

    def test_header_and_sections(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(2, "Bob", "queens", 714, 20),
            _row(3, "Charlie", "queens", 714, 30),
        ]
        out = daily_recap(TUE, scores, ENABLED)
        assert "Daily recap — Tue 14 Apr 2026" in out
        assert "Queens #714" in out
        assert "(5 pts)" in out
        assert "(4 pts)" in out
        assert "(3 pts)" in out
        assert "Day totals:" in out

    def test_day_totals_include_game_count(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(1, "Alice", "tango", 554, 20),
            _row(2, "Bob", "queens", 714, 20),
        ]
        out = daily_recap(TUE, scores, ENABLED)
        assert "Alice: 10 pts (2 games)" in out
        assert "Bob: 4 pts (1 game)" in out

    def test_disabled_game_excluded(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(1, "Alice", "pinpoint", 714, 3),
        ]
        enabled_no_pinpoint = frozenset({"queens"})
        out = daily_recap(TUE, scores, enabled_no_pinpoint)
        assert "Queens" in out
        assert "Pinpoint" not in out
        # Totals should only count queens
        assert "1 game" in out

    def test_pinpoint_renders_as_guesses(self):
        scores = [_row(1, "Alice", "pinpoint", 714, 3)]
        out = daily_recap(TUE, scores, frozenset({"pinpoint"}))
        assert "3 guesses" in out

    def test_mini_sudoku_display_name(self):
        scores = [_row(1, "Alice", "mini_sudoku", 246, 76)]
        out = daily_recap(TUE, scores, frozenset({"mini_sudoku"}))
        assert "Mini Sudoku #246" in out
        assert "1:16" in out


# ---------------------------------------------------------------------------
# weekly_wrap
# ---------------------------------------------------------------------------


class TestWeeklyWrap:
    def test_no_scores(self):
        out = weekly_wrap(MON, SUN, [], ENABLED)
        assert "No scores this week" in out

    def test_leaderboard_and_prizes(self):
        scores = [
            _row(1, "Alice", "queens", 714, 10, TUE),
            _row(2, "Bob", "queens", 714, 20, TUE),
            _row(1, "Alice", "tango", 554, 20, TUE),
            _row(2, "Bob", "tango", 554, 30, TUE),
        ]
        out = weekly_wrap(MON, SUN, scores, ENABLED)
        # Weekly wrap is: header + compact leaderboard + three prizes.
        # Champion / Wooden spoon / All-rounder were removed — they're
        # redundant with the leaderboard rows (top, bottom, "(N games)").
        assert "1. Alice" in out                      # leaderboard top
        assert "2. Bob" in out                        # leaderboard bottom
        assert "Most firsts" in out
        assert "Most lasts" in out
        assert "Champion" not in out
        assert "Wooden spoon" not in out
        assert "All-rounder" not in out
        # 4 submissions total, under the 5-submission threshold, so
        # nobody qualifies for Best average and the line is suppressed.
        assert "Best average" not in out

    def test_best_average_line_when_someone_qualifies(self):
        # Give Alice 5 submissions so she clears the threshold.
        scores = [
            _row(1, "Alice", "queens", 714, 10, TUE),
            _row(2, "Bob",   "queens", 714, 20, TUE),
            _row(1, "Alice", "tango",  554, 20, TUE),
            _row(1, "Alice", "zip",    393, 10, TUE),
            _row(1, "Alice", "patches", 28, 15, TUE),
            _row(1, "Alice", "mini_sudoku", 246, 60, TUE),
        ]
        out = weekly_wrap(MON, SUN, scores, ENABLED)
        assert "Best average" in out
        assert "Alice" in out
        assert "5 submissions" in out

    def test_wrap_is_concise_enough_to_forward(self):
        # The whole point of keeping the format tight: the wrap stays
        # short enough to forward from a 1:1 DM into the group as a
        # single message. With 6 players and 3 prizes that's ~10
        # non-empty lines. Cap at 15 to keep future drift honest.
        scores = []
        for i, name in enumerate(("Alice", "Bob", "Charlie", "Dee", "Evan", "Fiona"), start=1):
            scores.append(_row(i, name, "queens", 714, 10 + i * 5, TUE))
        out = weekly_wrap(MON, SUN, scores, ENABLED)
        nonempty = [ln for ln in out.splitlines() if ln.strip()]
        assert len(nonempty) <= 15, f"weekly wrap got long:\n{out}"

    def test_disabled_game_excluded_from_wrap(self):
        # Pinpoint submission should be filtered out of the aggregation
        # when pinpoint isn't in enabled_games. Alice's leaderboard
        # line shows "(1 game)" — if pinpoint had leaked through it
        # would say "(2 games)".
        scores = [
            _row(1, "Alice", "queens", 714, 10, TUE),
            _row(1, "Alice", "pinpoint", 714, 3, TUE),
        ]
        enabled_no_pinpoint = frozenset({"queens"})
        out = weekly_wrap(MON, SUN, scores, enabled_no_pinpoint)
        assert "Pinpoint" not in out
        assert "1. Alice" in out
        assert "(1 game)" in out

    def test_scores_outside_week_filtered(self):
        prev_sun = date(2026, 4, 12)
        scores = [
            _row(1, "Alice", "queens", 713, 10, prev_sun),
            _row(2, "Bob", "queens", 714, 20, TUE),
        ]
        out = weekly_wrap(MON, SUN, scores, ENABLED)
        assert "Bob" in out
        assert "Alice" not in out
