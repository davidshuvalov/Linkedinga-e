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
        # Weekly wrap is now just header + prize list — no detailed
        # leaderboard or game-winners section (kept short so it forwards
        # easily from the bot's DM into the group chat).
        assert "Leaderboard:" not in out
        assert "Game winners:" not in out
        assert "Champion" in out
        assert "All-rounder" in out
        assert "Most firsts" in out
        assert "Wooden spoon" in out
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
        # The whole point of the trim: the wrap body stays short. 10
        # lines is a loose upper bound (header + 5 prizes + blank line
        # + trailing blank); keeps future drift honest.
        scores = [
            _row(1, "Alice", "queens", 714, 10, TUE),
            _row(2, "Bob", "queens", 714, 20, TUE),
            _row(1, "Alice", "tango", 554, 20, TUE),
            _row(2, "Bob", "tango", 554, 30, TUE),
        ]
        out = weekly_wrap(MON, SUN, scores, ENABLED)
        # Count non-empty lines — should stay well under ~10.
        nonempty = [ln for ln in out.splitlines() if ln.strip()]
        assert len(nonempty) <= 10, f"weekly wrap got long:\n{out}"

    def test_disabled_game_excluded_from_wrap(self):
        # With only one enabled game and one player, Alice sweeps all
        # the prizes (including Wooden spoon — she's the only one).
        # The pinpoint row should be filtered out so it doesn't bleed
        # into the prize totals.
        scores = [
            _row(1, "Alice", "queens", 714, 10, TUE),
            _row(1, "Alice", "pinpoint", 714, 3, TUE),
        ]
        enabled_no_pinpoint = frozenset({"queens"})
        out = weekly_wrap(MON, SUN, scores, enabled_no_pinpoint)
        # Pinpoint is filtered out of the aggregation. Even though
        # "Queens" isn't rendered as a per-game winner any more (that
        # section is gone), Alice's "1 game" in the All-rounder line
        # proves pinpoint got excluded — two games enabled would have
        # given "2 games".
        assert "Pinpoint" not in out
        assert "All-rounder: Alice (1 game)" in out

    def test_scores_outside_week_filtered(self):
        prev_sun = date(2026, 4, 12)
        scores = [
            _row(1, "Alice", "queens", 713, 10, prev_sun),
            _row(2, "Bob", "queens", 714, 20, TUE),
        ]
        out = weekly_wrap(MON, SUN, scores, ENABLED)
        assert "Bob" in out
        assert "Alice" not in out
