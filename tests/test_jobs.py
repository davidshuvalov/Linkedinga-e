"""Tests for ``app.jobs`` — the scheduled-job orchestration layer.

Single cron entrypoint :func:`run_daily_recap` fires at 00:00 LA every
day. On Mon–Sat it emits the daily recap; on Sunday it emits the
weekly wrap. Both share the same I/O orchestration. Uses
:class:`InMemoryRepository` for data and patches ``send_recap`` to
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
from app.jobs import (
    maybe_fire_early_recap,
    render_daily,
    render_wrap,
    run_daily_recap,
    run_final_warning,
    run_morning_nudge,
)

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


# ---------------------------------------------------------------------------
# maybe_fire_early_recap
# ---------------------------------------------------------------------------


def _make_settings(games):
    return Settings(
        twilio_account_sid="ACfake",
        twilio_auth_token="fake_token",
        twilio_whatsapp_from="whatsapp:+14155238886",
        twilio_recap_to="whatsapp:+61400000099",
        supabase_url="",
        supabase_key="",
        timezone_name="Australia/Sydney",
        enabled_games=frozenset(games),
    )


def _seed(repo, player_id, name, day, games):
    """Seed ``player_id`` having played each game in ``games`` on ``day``."""
    repo.get_or_create_player(f"whatsapp:+6140000000{player_id}", name)
    for i, game in enumerate(games):
        repo.insert_score(
            player_id=player_id,
            game=game,
            puzzle_no=700 + i,
            puzzle_date=day,
            raw_score=10 + i,
            share_text="x",
        )


class TestMaybeFireEarlyRecap:
    """Event-driven helper: fire today's recap the moment everyone
    who's been active in the last 7 days has finished every enabled
    game. Marks ``recap_log`` so the cron knows to skip."""

    def test_does_nothing_when_no_active_players(self):
        repo = InMemoryRepository()
        settings = _make_settings({"queens"})
        now = datetime(2026, 4, 15, 18, 0, tzinfo=SYDNEY)
        result = maybe_fire_early_recap(repo, settings, now=now)
        assert result is None

    @patch("app.jobs.send_recap")
    def test_does_not_fire_when_someone_still_owes_a_game(self, mock_send):
        repo = InMemoryRepository()
        today = date(2026, 4, 14)
        # Alice played both, Bob only played queens — Bob still owes tango.
        _seed(repo, 1, "Alice", today, ["queens", "tango"])
        _seed(repo, 2, "Bob",   today, ["queens"])
        settings = _make_settings({"queens", "tango"})
        now = datetime(2026, 4, 14, 12, 0, tzinfo=LA)
        assert maybe_fire_early_recap(repo, settings, now=now) is None
        mock_send.assert_not_called()
        assert not repo.has_recap_been_sent(today, "daily")

    @patch("app.jobs.send_recap")
    def test_fires_when_everyone_done_and_marks_sent(self, mock_send):
        repo = InMemoryRepository()
        today = date(2026, 4, 14)
        _seed(repo, 1, "Alice", today, ["queens", "tango"])
        _seed(repo, 2, "Bob",   today, ["queens", "tango"])
        settings = _make_settings({"queens", "tango"})
        now = datetime(2026, 4, 14, 12, 0, tzinfo=LA)

        body = maybe_fire_early_recap(repo, settings, now=now)
        assert body is not None
        assert "Daily recap — Tue 14 Apr 2026" in body
        mock_send.assert_called_once()
        assert repo.has_recap_been_sent(today, "daily")

    @patch("app.jobs.send_recap")
    def test_does_not_double_fire(self, mock_send):
        repo = InMemoryRepository()
        today = date(2026, 4, 14)
        _seed(repo, 1, "Alice", today, ["queens"])
        settings = _make_settings({"queens"})
        now = datetime(2026, 4, 14, 12, 0, tzinfo=LA)

        first = maybe_fire_early_recap(repo, settings, now=now)
        second = maybe_fire_early_recap(repo, settings, now=now)
        assert first is not None
        assert second is None  # already sent
        assert mock_send.call_count == 1

    @patch("app.jobs.send_recap")
    def test_sunday_completion_fires_weekly_wrap(self, mock_send):
        repo = InMemoryRepository()
        sunday = date(2026, 4, 19)
        _seed(repo, 1, "Alice", sunday, ["queens"])
        settings = _make_settings({"queens"})
        now = datetime(2026, 4, 19, 12, 0, tzinfo=LA)

        body = maybe_fire_early_recap(repo, settings, now=now)
        assert body is not None
        assert "Weekly wrap" in body
        assert repo.has_recap_been_sent(sunday, "weekly")
        # The daily slot for Sunday is *not* marked — they're separate
        # types in recap_log so a Mon–Sat early-fire on a different day
        # wouldn't accidentally suppress this one.
        assert not repo.has_recap_been_sent(sunday, "daily")


class TestRunDailyRecapSkipsAfterEarlyFire:
    """The cron sees the recap_log and bails out without re-sending."""

    @patch("app.jobs.send_recap")
    def test_cron_skips_when_recap_already_sent(self, mock_send):
        repo = InMemoryRepository()
        target_day = date(2026, 4, 14)
        repo.mark_recap_sent(target_day, "daily")
        # Cron fires at LA midnight on Apr 15, intends to recap Apr 14.
        now = datetime(2026, 4, 15, 0, 0, tzinfo=LA)
        body = run_daily_recap(repo, SETTINGS, now=now)
        assert body is None
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# run_morning_nudge
# ---------------------------------------------------------------------------


class TestMorningNudge:
    """Cron at 08:30 Sydney. DMs each opted-in active player with the
    games they haven't played yet today."""

    @patch("app.jobs.send_dm")
    def test_no_active_players_does_nothing(self, mock_dm):
        repo = InMemoryRepository()
        settings = _make_settings({"queens", "tango"})
        now = datetime(2026, 4, 14, 8, 30, tzinfo=SYDNEY)
        nudged = run_morning_nudge(repo, settings, now=now)
        assert nudged == []
        mock_dm.assert_not_called()

    @patch("app.jobs.send_dm")
    def test_skips_player_already_done(self, mock_dm):
        # Alice has played both enabled games today (LA); should not
        # be nudged. ``la_date(now)`` for an 08:30 Sydney morning is
        # the *previous* LA calendar day (mid-afternoon LA the day
        # before — the puzzle hasn't rolled yet), so seed against
        # that date.
        from app.puzzles import la_date
        repo = InMemoryRepository()
        now = datetime(2026, 4, 14, 8, 30, tzinfo=SYDNEY)
        today_la = la_date(now)
        _seed(repo, 1, "Alice", today_la, ["queens", "tango"])
        settings = _make_settings({"queens", "tango"})
        nudged = run_morning_nudge(repo, settings, now=now)
        assert nudged == []
        mock_dm.assert_not_called()

    @patch("app.jobs.send_dm")
    def test_nudges_player_with_outstanding_games(self, mock_dm):
        # Bob played the previous LA day (so he counts as active)
        # but nothing on today's LA day — expect a nudge listing
        # both enabled games.
        from app.puzzles import la_date
        mock_dm.return_value = True
        repo = InMemoryRepository()
        now = datetime(2026, 4, 14, 8, 30, tzinfo=SYDNEY)
        today_la = la_date(now)
        prev_la = today_la - timedelta(days=1)
        _seed(repo, 1, "Bob", prev_la, ["queens"])
        settings = _make_settings({"queens", "tango"})
        nudged = run_morning_nudge(repo, settings, now=now)
        assert nudged == ["whatsapp:+61400000001"]
        mock_dm.assert_called_once()
        body = mock_dm.call_args[0][2]
        assert "Bob" in body
        assert "Queens" in body
        assert "Tango" in body

    @patch("app.jobs.send_dm")
    def test_skips_opted_out_players(self, mock_dm):
        from app.puzzles import la_date
        repo = InMemoryRepository()
        now = datetime(2026, 4, 14, 8, 30, tzinfo=SYDNEY)
        prev_la = la_date(now) - timedelta(days=1)
        bob = repo.get_or_create_player("whatsapp:+61400000001", "Bob")
        repo.insert_score(
            player_id=bob.id, game="queens", puzzle_no=713,
            puzzle_date=prev_la, raw_score=10, share_text="x",
        )
        repo.set_notifications_enabled(bob.id, False)
        settings = _make_settings({"queens", "tango"})
        nudged = run_morning_nudge(repo, settings, now=now)
        assert nudged == []
        mock_dm.assert_not_called()

    @patch("app.jobs.send_dm")
    def test_partial_progress_lists_only_missing_games(self, mock_dm):
        from app.puzzles import la_date
        mock_dm.return_value = True
        repo = InMemoryRepository()
        now = datetime(2026, 4, 14, 8, 30, tzinfo=SYDNEY)
        today_la = la_date(now)
        # Charlie played Queens today (LA), Tango still owed.
        _seed(repo, 1, "Charlie", today_la, ["queens"])
        settings = _make_settings({"queens", "tango"})
        run_morning_nudge(repo, settings, now=now)
        body = mock_dm.call_args[0][2]
        # "Done so far" should list Queens; "Still to play" lists Tango.
        assert "Queens" in body.split("Still to play:")[0]
        assert "Tango" in body.split("Still to play:")[1]


# ---------------------------------------------------------------------------
# run_final_warning — 15 minutes before midnight LA
# ---------------------------------------------------------------------------


class TestFinalWarning:
    """Cron at 23:45 LA. Mirrors morning nudge but with rude copy."""

    @patch("app.jobs.send_dm")
    def test_no_active_players_does_nothing(self, mock_dm):
        repo = InMemoryRepository()
        settings = _make_settings({"queens", "tango"})
        now = datetime(2026, 4, 14, 23, 45, tzinfo=LA)
        warned = run_final_warning(repo, settings, now=now)
        assert warned == []
        mock_dm.assert_not_called()

    @patch("app.jobs.send_dm")
    def test_skips_player_already_done(self, mock_dm):
        from app.puzzles import la_date
        repo = InMemoryRepository()
        now = datetime(2026, 4, 14, 23, 45, tzinfo=LA)
        today_la = la_date(now)
        _seed(repo, 1, "Alice", today_la, ["queens", "tango"])
        settings = _make_settings({"queens", "tango"})
        warned = run_final_warning(repo, settings, now=now)
        assert warned == []
        mock_dm.assert_not_called()

    @patch("app.jobs.send_dm")
    def test_warns_player_with_outstanding_games(self, mock_dm):
        from app.puzzles import la_date
        mock_dm.return_value = True
        repo = InMemoryRepository()
        now = datetime(2026, 4, 14, 23, 45, tzinfo=LA)
        today_la = la_date(now)
        prev_la = today_la - timedelta(days=1)
        # Bob is active (played yesterday) but hasn't played today.
        _seed(repo, 1, "Bob", prev_la, ["queens"])
        settings = _make_settings({"queens", "tango"})
        warned = run_final_warning(repo, settings, now=now)
        assert warned == ["whatsapp:+61400000001"]
        body = mock_dm.call_args[0][2]
        assert "Bob" in body
        # Both missing games should be called out by name.
        assert "Queens" in body
        assert "Tango" in body

    @patch("app.jobs.send_dm")
    def test_skips_opted_out_players(self, mock_dm):
        from app.puzzles import la_date
        repo = InMemoryRepository()
        now = datetime(2026, 4, 14, 23, 45, tzinfo=LA)
        prev_la = la_date(now) - timedelta(days=1)
        bob = repo.get_or_create_player("whatsapp:+61400000001", "Bob")
        repo.insert_score(
            player_id=bob.id, game="queens", puzzle_no=713,
            puzzle_date=prev_la, raw_score=10, share_text="x",
        )
        repo.set_notifications_enabled(bob.id, False)
        settings = _make_settings({"queens", "tango"})
        warned = run_final_warning(repo, settings, now=now)
        assert warned == []
        mock_dm.assert_not_called()

    @patch("app.jobs.send_dm")
    def test_lists_only_missing_games(self, mock_dm):
        from app.puzzles import la_date
        mock_dm.return_value = True
        repo = InMemoryRepository()
        now = datetime(2026, 4, 14, 23, 45, tzinfo=LA)
        today_la = la_date(now)
        _seed(repo, 1, "Charlie", today_la, ["queens"])  # tango still owed
        settings = _make_settings({"queens", "tango"})
        run_final_warning(repo, settings, now=now)
        body = mock_dm.call_args[0][2]
        # Message should name the owed game (Tango) but not the
        # already-completed one (Queens).
        assert "Tango" in body
        assert "Queens" not in body
