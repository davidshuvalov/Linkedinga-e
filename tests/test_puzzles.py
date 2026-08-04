"""Tests for ``app.puzzles``.

The epoch math is the bit that tells us which puzzle LinkedIn is
currently serving, so:

- Plain day-delta arithmetic across LA dates.
- Sydney-evening / LA-midnight boundary: submissions just before vs just
  after the LA flip must land on different puzzle numbers.
- US daylight-saving transitions: the flip hour drifts an hour across
  a DST boundary, which is the exact scenario ``zoneinfo`` handles for
  us — assert it does so we don't accidentally regress.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.parsers import GAMES
from app.puzzles import PUZZLE_EPOCH, expected_puzzle_no, la_date, week_bounds

SYDNEY = ZoneInfo("Australia/Sydney")
LA = ZoneInfo("America/Los_Angeles")


def _la_noon(day: date) -> datetime:
    """Midday LA on ``day`` — far enough from either midnight that no
    DST transition can shift which LA date it lands on."""
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=LA)


class TestEpochCoverage:
    def test_every_game_has_an_epoch(self):
        assert set(PUZZLE_EPOCH) == set(GAMES)

    def test_games_may_pin_different_reference_dates(self):
        """Each game's epoch is independent, and at least one game
        genuinely differs.

        Most games were pinned together on 2026-04-22, but Wend
        launched later and is pinned to its own first-puzzle date.
        The delta arithmetic is per-game, so a shared anchor is never
        safe to assume — the tests below anchor on each game's own
        reference date for exactly this reason.
        """
        ref_dates = {ref_date for ref_date, _ in PUZZLE_EPOCH.values()}
        assert len(ref_dates) > 1

    def test_wend_epoch_is_pinned_to_its_launch(self):
        """Pinned deliberately: Wend #1 was confirmed live on
        2026-06-09 (back-solved from Wend #8 on 2026-06-16 and
        cross-checked against Patches). Changing this shifts every
        Wend submission onto a different day, so it should only move
        with fresh evidence from a real share."""
        assert PUZZLE_EPOCH["wend"] == (date(2026, 6, 9), 1)


class TestLaDate:
    def test_sydney_morning_is_previous_la_day(self):
        # 10am Sydney on 22 Apr 2026 = 5pm LA on 21 Apr 2026.
        ts = datetime(2026, 4, 22, 10, 0, tzinfo=SYDNEY)
        assert la_date(ts) == date(2026, 4, 21)

    def test_sydney_evening_after_rollover_is_current_la_day(self):
        # 6pm Sydney on 22 Apr 2026 is past the LA midnight flip.
        ts = datetime(2026, 4, 22, 18, 0, tzinfo=SYDNEY)
        assert la_date(ts) == date(2026, 4, 22)

    def test_la_date_from_utc_matches(self):
        ts = datetime(2026, 4, 22, 8, 0, tzinfo=ZoneInfo("UTC"))
        # 8am UTC = 1am LA on same date
        assert la_date(ts) == date(2026, 4, 22)


class TestExpectedPuzzleNo:
    """Each case anchors on the game's *own* reference date. Anchoring
    every game on one shared date only worked while they happened to
    share one, and broke the moment Wend was pinned to its real launch
    day — a false failure that said nothing about the epoch math."""

    def test_reference_date_returns_reference_number(self):
        for game, (ref_date, ref_no) in PUZZLE_EPOCH.items():
            assert expected_puzzle_no(game, _la_noon(ref_date)) == ref_no

    def test_one_day_later_increments_by_one(self):
        for game, (ref_date, ref_no) in PUZZLE_EPOCH.items():
            ts = _la_noon(ref_date + timedelta(days=1))
            assert expected_puzzle_no(game, ts) == ref_no + 1

    def test_one_day_earlier_decrements_by_one(self):
        for game, (ref_date, ref_no) in PUZZLE_EPOCH.items():
            ts = _la_noon(ref_date - timedelta(days=1))
            assert expected_puzzle_no(game, ts) == ref_no - 1

    def test_sydney_4pm_is_still_yesterdays_puzzle(self):
        # 4pm Sydney on 23 Apr 2026 = 11pm LA on 22 Apr (still today in LA).
        # Queens epoch pins 22 Apr LA → #722, so this Sydney time also
        # resolves to #722.
        ts = datetime(2026, 4, 23, 16, 0, tzinfo=SYDNEY)
        assert expected_puzzle_no("queens", ts) == 722

    def test_sydney_6pm_is_next_days_puzzle(self):
        # 6pm Sydney on 23 Apr = 1am LA on 23 Apr (past midnight rollover),
        # so the next LA day → one more than the 22 Apr epoch.
        ts = datetime(2026, 4, 23, 18, 0, tzinfo=SYDNEY)
        assert expected_puzzle_no("queens", ts) == 723

    def test_unknown_game_raises(self):
        ts = datetime(2026, 4, 22, 12, 0, tzinfo=LA)
        with pytest.raises(KeyError):
            expected_puzzle_no("not_a_real_game", ts)

    def test_weekly_span(self):
        """Day deltas work cleanly a month either side of each game's
        own epoch, including across the DST boundaries in that span."""
        for game, (ref_date, ref_no) in PUZZLE_EPOCH.items():
            for delta in range(-30, 31):
                ts = _la_noon(ref_date + timedelta(days=delta))
                assert expected_puzzle_no(game, ts) == ref_no + delta


class TestDstBoundary:
    """US DST ends on the first Sunday of November (fall back 2→1am) and
    starts on the second Sunday of March (spring forward 2→3am). The
    puzzle rollover is tied to local LA midnight, so ``zoneinfo`` quietly
    handles the hour drift — but we pin the behaviour here."""

    def test_across_dst_end_2026(self):
        # DST ends 2026-11-01 at 02:00 LA (fall back). An 8pm Sydney
        # submission on 2026-11-01 = 1am LA on 2026-11-01 (already past
        # midnight PDT→PST flip), so LA date is 2026-11-01.
        ts = datetime(2026, 11, 1, 20, 0, tzinfo=SYDNEY)
        assert la_date(ts) == date(2026, 11, 1)
        # Puzzle number is ref + 193 (2026-11-01 is 193 days after 2026-04-22).
        days = (date(2026, 11, 1) - date(2026, 4, 22)).days
        assert expected_puzzle_no("queens", ts) == 722 + days

    def test_across_dst_start_2027(self):
        # DST starts 2027-03-14. 7pm Sydney on that day = midnight LA
        # region — test a safe 6pm LA value instead to avoid ambiguity.
        ts = datetime(2027, 3, 14, 18, 0, tzinfo=LA)
        days = (date(2027, 3, 14) - date(2026, 4, 22)).days
        assert expected_puzzle_no("queens", ts) == 722 + days


class TestWeekBounds:
    """``week_bounds`` is pure calendar math — no timezone awareness —
    but callers pass LA-anchored dates so the week aligns with
    LinkedIn's Mon–Sun puzzle week."""

    def test_monday_returns_itself_and_sunday(self):
        monday = date(2026, 4, 13)
        start, end = week_bounds(monday)
        assert start == monday
        assert end == date(2026, 4, 19)

    def test_sunday_stays_in_same_week(self):
        sunday = date(2026, 4, 19)
        start, end = week_bounds(sunday)
        assert start == date(2026, 4, 13)
        assert end == sunday

    def test_midweek(self):
        wed = date(2026, 4, 15)
        start, end = week_bounds(wed)
        assert start == date(2026, 4, 13)
        assert end == date(2026, 4, 19)
