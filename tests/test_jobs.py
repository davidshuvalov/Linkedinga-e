"""Tests for ``app.jobs`` — the scheduled-job orchestration layer.

Single cron entrypoint :func:`run_daily_recap` fires at 00:00 LA every
day. On Mon–Sat it emits the daily recap; on Sunday it emits the
weekly wrap. Both share the same I/O orchestration. Uses
:class:`InMemoryRepository` for data and patches ``send_recap`` to
capture what would be sent without actually calling Twilio.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.cli import _seed_demo
from app.config import Settings
from app.db import InMemoryRepository
from app.jobs import render_daily, render_wrap, run_daily_recap

SYDNEY = ZoneInfo("Australia/Sydney")
LA = ZoneInfo("America/Los_Angeles")

SETTINGS = Settings(
    twilio_account_sid="ACfake",
    twilio_auth_token="fake_token",
    twilio_whatsapp_from="whatsapp:+14155238886",
    twilio_recap_to="whatsapp:+61400000099",
    supabase_url="",
    supabase_key="",
    timezone_name="Australia/Sydney",
    enabled_games=frozenset(
        {"queens", "tango", "zip", "patches", "mini_sudoku"}
    ),
)


@pytest.fixture
def seeded_repo() -> InMemoryRepository:
    repo = InMemoryRepository()
    _seed_demo(repo, today=date(2026, 4, 14))
    return repo


class TestRunDailyRecap:
    """The cron fires at 00:00 LA daily and recaps the day that just
    closed (``la_date(now) - 1 day``)."""

    @patch("app.jobs.send_recap")
    def test_midweek_recap_emits_daily_format(self, mock_send, seeded_repo):
        # Fires at the Apr 15 LA flip — closes out Apr 14 LA (a Tuesday).
        now = datetime(2026, 4, 15, 0, 0, tzinfo=LA)
        body = run_daily_recap(seeded_repo, SETTINGS, now=now)

        assert "Daily recap — Tue 14 Apr 2026" in body
        assert "Queens #714" in body
        # Daily includes running "Week so far" leaderboard.
        assert "Week so far" in body
        # Daily does NOT include the weekly-wrap-only sections.
        assert "Game winners" not in body
        assert "Prizes" not in body
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][1] == body
        assert len(call_args[1]["dm_targets"]) > 0

    @patch("app.jobs.send_recap")
    def test_sunday_close_emits_weekly_wrap_format(self, mock_send, seeded_repo):
        # Apr 14 is Tue → Sunday of that week is Apr 19. Cron fires at
        # Monday Apr 20 00:00 LA, closing Sunday Apr 19. Should produce
        # the weekly wrap (not a plain daily) — single message.
        now = datetime(2026, 4, 20, 0, 0, tzinfo=LA)
        body = run_daily_recap(seeded_repo, SETTINGS, now=now)

        assert "Weekly wrap — Mon 13 Apr to Sun 19 Apr 2026" in body
        # Weekly wrap surfaces the final-day per-game results, the week
        # totals, the per-game weekly winners, and the three prizes.
        assert "Sun 19 Apr" in body
        assert "Week totals" in body
        assert "Game winners" in body
        assert "Most firsts" in body
        mock_send.assert_called_once()

    @patch("app.jobs.send_recap")
    def test_sydney_wallclock_equivalent_also_works(self, mock_send, seeded_repo):
        # 17:00 Sydney AEST on Apr 15 = 00:00 PDT on Apr 15 LA.
        now = datetime(2026, 4, 15, 17, 0, tzinfo=SYDNEY)
        body = run_daily_recap(seeded_repo, SETTINGS, now=now)

        assert "Tue 14 Apr 2026" in body
        assert "Queens #714" in body

    @patch("app.jobs.send_recap")
    def test_empty_day_still_sends(self, mock_send):
        repo = InMemoryRepository()
        now = datetime(2026, 1, 2, 0, 0, tzinfo=LA)
        body = run_daily_recap(repo, SETTINGS, now=now)

        assert "No scores yet" in body
        mock_send.assert_called_once()


class TestRenderHelpers:
    """``render_daily`` / ``render_wrap`` are the on-demand entry points
    used by the webhook ``recap`` and ``wrap`` commands."""

    def test_render_daily_for_midweek(self, seeded_repo):
        body, dm_targets = render_daily(seeded_repo, SETTINGS, date(2026, 4, 14))
        assert "Daily recap — Tue 14 Apr 2026" in body
        assert "Week so far" in body
        assert dm_targets  # seeded data has active players this week

    def test_render_daily_for_sunday_yields_wrap(self, seeded_repo):
        # Sunday is the magic day where render_daily switches format.
        body, _ = render_daily(seeded_repo, SETTINGS, date(2026, 4, 19))
        assert "Weekly wrap" in body
        assert "Game winners" in body

    def test_render_wrap_works_midweek(self, seeded_repo):
        # Lets a user pull the wrap snapshot from any day in the week,
        # showing partial leaderboard / prizes for the in-progress week.
        body, _ = render_wrap(seeded_repo, SETTINGS, date(2026, 4, 14))
        assert "Weekly wrap" in body
        assert "Mon 13 Apr to Sun 19 Apr 2026" in body
