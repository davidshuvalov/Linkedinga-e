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
    @patch("app.jobs.send_recap")
    def test_formats_and_sends_todays_recap(self, mock_send, seeded_repo):
        now = datetime(2026, 4, 14, 21, 0, tzinfo=SYDNEY)
        body = run_daily_recap(seeded_repo, SETTINGS, now=now)

        assert "Daily recap" in body
        assert "Tue 14 Apr 2026" in body
        assert "Queens #714" in body
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][1] == body  # second positional arg is body
        assert len(call_args[1]["dm_targets"]) > 0

    @patch("app.jobs.send_recap")
    def test_empty_day_still_sends(self, mock_send):
        repo = InMemoryRepository()
        now = datetime(2026, 1, 1, 21, 0, tzinfo=SYDNEY)
        body = run_daily_recap(repo, SETTINGS, now=now)

        assert "No scores yet" in body
        mock_send.assert_called_once()


class TestRunWeeklyWrap:
    @patch("app.jobs.send_recap")
    def test_formats_and_sends_this_weeks_wrap(self, mock_send, seeded_repo):
        now = datetime(2026, 4, 19, 20, 0, tzinfo=SYDNEY)  # Sunday
        body = run_weekly_wrap(seeded_repo, SETTINGS, now=now)

        assert "Weekly wrap" in body
        assert "Leaderboard" in body
        assert "Prizes" in body
        assert "Champion" in body
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][1] == body
        assert len(call_args[1]["dm_targets"]) > 0

    @patch("app.jobs.send_recap")
    def test_week_bounds_computed_from_now(self, mock_send, seeded_repo):
        # Wednesday of the same week — should still include Mon–Sun data
        now = datetime(2026, 4, 15, 20, 0, tzinfo=SYDNEY)
        body = run_weekly_wrap(seeded_repo, SETTINGS, now=now)

        assert "Mon 13 Apr" in body
        assert "Sun 19 Apr" in body
        assert "Alice" in body  # seeded data falls in this week
