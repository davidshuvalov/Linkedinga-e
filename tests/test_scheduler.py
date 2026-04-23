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
        # 3-player round with r12 = 2.0 routes through competitive_score
        # (clear-winner branch). Rather than pinning exact float values
        # the test checks the shape the recap should have: each player
        # present with a ``(N pts)`` suffix and descending points.
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(2, "Bob", "queens", 714, 20),
            _row(3, "Charlie", "queens", 714, 30),
        ]
        out = daily_recap(TUE, scores, ENABLED)
        assert "Daily recap — Tue 14 Apr 2026" in out
        assert "Queens #714" in out
        # 1st place always hits the +2 cap under competitive_score:
        # base 5 + 2 = 7.0 pts. Intermediate values depend on the
        # proportional debit; assert "pts" appears 3x for sanity.
        assert out.count(" pts)") == 3
        assert "(7 pts)" in out  # 7.0 → "7 pts" under the clean formatter
        # "Day totals" was replaced by the cumulative "Week so far"
        # leaderboard — same numbers but framed across the whole week.
        assert "Week so far:" in out

    def test_legacy_fallback_for_two_player_round(self):
        # Below competitive_score's 3-player floor; recap should keep
        # showing integer rank points (5 / 4).
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(2, "Bob",   "queens", 714, 20),
        ]
        out = daily_recap(TUE, scores, ENABLED)
        assert "(5 pts)" in out
        assert "(4 pts)" in out

    def test_day_totals_include_round_count(self):
        # Suffix is submissions count ("rounds"), not distinct game
        # types — users replaying the same game daily want that to
        # grow linearly instead of topping out at 7.
        scores = [
            _row(1, "Alice", "queens", 714, 10),
            _row(1, "Alice", "tango", 554, 20),
            _row(2, "Bob", "queens", 714, 20),
        ]
        out = daily_recap(TUE, scores, ENABLED)
        assert "Alice: 10 pts (2 rounds)" in out
        assert "Bob: 4 pts (1 round)" in out

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
        assert "1 round" in out

    def test_pinpoint_renders_as_guesses(self):
        scores = [_row(1, "Alice", "pinpoint", 714, 3)]
        out = daily_recap(TUE, scores, frozenset({"pinpoint"}))
        assert "3 guesses" in out

    def test_mini_sudoku_display_name(self):
        scores = [_row(1, "Alice", "mini_sudoku", 246, 76)]
        out = daily_recap(TUE, scores, frozenset({"mini_sudoku"}))
        assert "Mini Sudoku #246" in out
        assert "1:16" in out

    def test_week_so_far_aggregates_prior_days(self):
        # daily_recap takes the WHOLE week's scores so the running
        # total spans Monday-through-today, not just today. Tuesday
        # recap should include Monday's points in "Week so far".
        scores = [
            _row(1, "Alice", "queens", 713, 10, MON),  # Mon win → 5 pts
            _row(2, "Bob",   "queens", 713, 20, MON),  # Mon → 4 pts
            _row(1, "Alice", "tango",  554, 20, TUE),  # Tue solo → no first/last
            _row(2, "Bob",   "tango",  554, 30, TUE),
        ]
        out = daily_recap(TUE, scores, ENABLED)
        # Tuesday's per-game shows (Tango).
        assert "Tango #554" in out
        # Cumulative leaderboard reflects Mon + Tue.
        # Alice: 5 (Mon Queens) + 5 (Tue Tango win) = 10
        # Bob:   4 (Mon Queens) + 4 (Tue Tango)     = 8
        assert "Week so far:" in out
        assert "1. Alice: 10 pts" in out
        assert "2. Bob: 8 pts" in out

    def test_per_game_running_totals_section(self):
        # Per-game running point totals for the week — one line per
        # game with each player's cumulative points in that game. Lets
        # players see who's ahead in Queens specifically, not just on
        # aggregate.
        scores = [
            # Queens Mon: Alice 5, Bob 4
            _row(1, "Alice", "queens", 713, 10, MON),
            _row(2, "Bob",   "queens", 713, 20, MON),
            # Queens Tue: Bob 5, Alice 4 → weekly Queens = Alice 9, Bob 9
            _row(1, "Alice", "queens", 714, 30, TUE),
            _row(2, "Bob",   "queens", 714, 20, TUE),
            # Tango Tue: Alice 5, Bob 4
            _row(1, "Alice", "tango",  554, 10, TUE),
            _row(2, "Bob",   "tango",  554, 20, TUE),
        ]
        out = daily_recap(TUE, scores, ENABLED)
        assert "Game standings (week):" in out
        # Tie at the top sorts lower player_id first (Alice before Bob).
        assert "Queens: Alice 9, Bob 9" in out
        assert "Tango: Alice 5, Bob 4" in out

    def test_missing_today_nudge_names_absent_regulars(self):
        # Alice played Mon, nobody played Tue. Tuesday recap should
        # name Alice as missing, since she was active earlier this week.
        scores = [
            _row(1, "Alice", "queens", 713, 10, MON),
            _row(2, "Bob",   "queens", 714, 20, TUE),
        ]
        out = daily_recap(TUE, scores, ENABLED)
        assert "Alice" in out
        # The nudge line always mentions the name immediately after a
        # passive-aggressive phrase — Alice should appear outside the
        # Monday-only block, i.e. somewhere below the Week so far section.
        nudge_section = out.split("Week so far:")[-1]
        assert "Alice" in nudge_section

    def test_no_nudge_when_everyone_played_today(self):
        # Both Alice and Bob submitted today; nobody is a "missing
        # regular", so the passive-aggressive line is suppressed.
        scores = [
            _row(1, "Alice", "queens", 713, 10, MON),
            _row(2, "Bob",   "queens", 713, 20, MON),
            _row(1, "Alice", "queens", 714, 10, TUE),
            _row(2, "Bob",   "queens", 714, 20, TUE),
        ]
        out = daily_recap(TUE, scores, ENABLED)
        # None of the known template starters should appear.
        for phrase in (
            "MIA today",
            "Still MIA",
            "no-shows",
            "Haven't heard",
            "Where art thou",
            "Benched today",
        ):
            assert phrase not in out

    def test_per_game_running_totals_excludes_disabled(self):
        scores = [
            _row(1, "Alice", "queens",   713, 10, MON),
            _row(1, "Alice", "pinpoint", 713, 3,  MON),
        ]
        enabled_no_pinpoint = frozenset({"queens"})
        out = daily_recap(MON, scores, enabled_no_pinpoint)
        assert "Game standings (week):" in out
        assert "Queens:" in out
        assert "Pinpoint" not in out


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

    def test_fastest_total_time_prize_line(self):
        # Alice plays 5 time-based rounds totalling 115s; Bob has a
        # single sub-second run. Alice qualifies (≥5 rounds) and has
        # the lowest total among eligible players, so she wins.
        scores = [
            _row(1, "Alice", "queens", 714, 10, TUE),
            _row(2, "Bob",   "queens", 714, 5,  TUE),
            _row(1, "Alice", "tango",  554, 20, TUE),
            _row(1, "Alice", "zip",    393, 10, TUE),
            _row(1, "Alice", "patches", 28, 15, TUE),
            _row(1, "Alice", "mini_sudoku", 246, 60, TUE),
        ]
        out = weekly_wrap(MON, SUN, scores, ENABLED)
        assert "Fastest total time" in out
        assert "Alice" in out
        # 10+20+10+15+60 = 115s → formatted as 1:55.
        assert "1:55" in out
        assert "5 rounds" in out

    def test_fastest_total_time_suppressed_when_nobody_qualifies(self):
        # Neither player clears the 5-round floor → line suppressed.
        scores = [
            _row(1, "Alice", "queens", 714, 10, TUE),
            _row(2, "Bob",   "queens", 714, 20, TUE),
        ]
        out = weekly_wrap(MON, SUN, scores, ENABLED)
        assert "Fastest total time" not in out

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
        # line shows "(1 round)" — if pinpoint had leaked through it
        # would say "(2 rounds)".
        scores = [
            _row(1, "Alice", "queens", 714, 10, TUE),
            _row(1, "Alice", "pinpoint", 714, 3, TUE),
        ]
        enabled_no_pinpoint = frozenset({"queens"})
        out = weekly_wrap(MON, SUN, scores, enabled_no_pinpoint)
        assert "Pinpoint" not in out
        assert "1. Alice" in out
        assert "(1 round)" in out

    def test_scores_outside_week_filtered(self):
        prev_sun = date(2026, 4, 12)
        scores = [
            _row(1, "Alice", "queens", 713, 10, prev_sun),
            _row(2, "Bob", "queens", 714, 20, TUE),
        ]
        out = weekly_wrap(MON, SUN, scores, ENABLED)
        assert "Bob" in out
        assert "Alice" not in out

    def test_includes_sunday_per_game_section(self):
        # The wrap is now a "super daily" — it shows the final day's
        # per-game results in addition to the week totals + winners +
        # prizes. Sunday Apr 19's Queens round should appear with
        # ranked players.
        scores = [
            _row(1, "Alice", "queens", 713, 10, TUE),
            _row(1, "Alice", "queens", 719, 30, SUN),
            _row(2, "Bob",   "queens", 719, 35, SUN),
        ]
        out = weekly_wrap(MON, SUN, scores, ENABLED)
        # Final-day per-game block should be present + dated.
        assert "Sun 19 Apr" in out
        assert "Queens #719" in out
        # Week totals leaderboard also present.
        assert "Week totals:" in out
        # Per-game weekly winners section.
        assert "Game winners:" in out
