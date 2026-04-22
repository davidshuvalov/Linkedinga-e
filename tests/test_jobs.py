"""Tests for ``app.jobs`` — the scheduled-job orchestration layer.

Uses :class:`InMemoryRepository` for data and patches ``send_recap`` to
capture what would be sent without actually calling Twilio.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.cli import _seed_demo
from app.config import Settings
from app.db import InMemoryRepository
from app.jobs import run_daily_recap, run_weekly_wrap

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
    """Fires at 00:00 LA — recaps the LA day that just closed
    (``la_date(now) - 1 day``). To recap the seeded Apr 14 data, the
    job must be called at/after Apr 15 00:00 LA."""

    @patch("app.jobs.send_recap")
    def test_recap_closes_yesterday_la(self, mock_send, seeded_repo):
        # Fires at the Apr 15 LA flip — closes out Apr 14 LA.
        now = datetime(2026, 4, 15, 0, 0, tzinfo=LA)
        body = run_daily_recap(seeded_repo, SETTINGS, now=now)

        assert "Daily recap" in body
        assert "Tue 14 Apr 2026" in body
        assert "Queens #714" in body
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][1] == body  # second positional arg is body
        assert len(call_args[1]["dm_targets"]) > 0

    @patch("app.jobs.send_recap")
    def test_sydney_wallclock_equivalent_also_works(self, mock_send, seeded_repo):
        # 17:00 Sydney AEST on Apr 15 = 00:00 PDT on Apr 15 LA: same
        # instant as the cron fire, confirming tz arithmetic is correct.
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


class TestRunWeeklyWrap:
    """Fires at Monday 00:01 LA — wraps the LA Mon–Sun week whose
    Sunday just ended."""

    @patch("app.jobs.send_recap")
    def test_wraps_the_just_closed_week(self, mock_send, seeded_repo):
        # Apr 14 is Tue, so its Sunday is Apr 19. The week wraps at
        # Monday Apr 20 00:01 LA. Seeded scores on Apr 13–14 fall
        # inside that week.
        now = datetime(2026, 4, 20, 0, 1, tzinfo=LA)
        body = run_weekly_wrap(seeded_repo, SETTINGS, now=now)

        assert "Weekly wrap" in body
        assert "Mon 13 Apr" in body
        assert "Sun 19 Apr" in body
        # Wrap is now a concise prize list (no Leaderboard or Game
        # winners sections). Champion line should name Alice.
        assert "Champion: Alice" in body
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][1] == body
        assert len(call_args[1]["dm_targets"]) > 0

    @patch("app.jobs.send_recap")
    def test_sydney_monday_evening_equivalent(self, mock_send, seeded_repo):
        # Sydney equivalent of the Apr 20 00:01 LA cron fire.
        now = datetime(2026, 4, 20, 17, 1, tzinfo=SYDNEY)
        body = run_weekly_wrap(seeded_repo, SETTINGS, now=now)

        assert "Mon 13 Apr" in body
        assert "Sun 19 Apr" in body
