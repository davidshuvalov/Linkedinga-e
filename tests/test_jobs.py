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
    PRE_RESET_STAGES,
    maybe_fire_early_recap,
    render_daily,
    render_wrap,
    run_daily_recap,
    run_morning_nudge,
    run_new_games_announcement,
    run_pre_reset_warning,
    run_weekly_wrap_early,
    send_champion_loser_dms,
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


class TestWeeklyWrapEarly:
    """``run_weekly_wrap_early`` fires at Sun 23:59 LA — one minute
    before the new puzzle drop. Lands the wrap before the next-day
    new-games message. Idempotent via ``recap_log`` so the Mon 00:00
    LA daily_recap cron skips silently when the early wrap already
    sent."""

    @patch("app.jobs.send_recap")
    def test_fires_on_sunday_la_at_2359(self, mock_send, seeded_repo):
        # Sun 19 Apr 2026 at 23:59 LA. la_date(now) == Sunday.
        now = datetime(2026, 4, 19, 23, 59, tzinfo=LA)
        body = run_weekly_wrap_early(seeded_repo, SETTINGS, now=now)
        assert body is not None
        assert "Weekly wrap — Mon 13 Apr to Sun 19 Apr 2026" in body
        mock_send.assert_called_once()

    @patch("app.jobs.send_recap")
    def test_silent_on_non_sunday(self, mock_send, seeded_repo):
        # Tuesday — even if the cron is invoked manually, it bails
        # rather than rendering a daily recap.
        now = datetime(2026, 4, 14, 23, 59, tzinfo=LA)
        body = run_weekly_wrap_early(seeded_repo, SETTINGS, now=now)
        assert body is None
        mock_send.assert_not_called()

    @patch("app.jobs.send_recap")
    def test_idempotent_when_already_sent(self, mock_send, seeded_repo):
        # First run sends; second run is a no-op (recap_log entry).
        now = datetime(2026, 4, 19, 23, 59, tzinfo=LA)
        run_weekly_wrap_early(seeded_repo, SETTINGS, now=now)
        mock_send.reset_mock()
        body = run_weekly_wrap_early(seeded_repo, SETTINGS, now=now)
        assert body is None
        mock_send.assert_not_called()

    @patch("app.jobs.send_recap")
    def test_monday_daily_recap_skips_after_early_wrap(self, mock_send, seeded_repo):
        # Sun 23:59 LA fires the wrap; the Mon 00:00 LA daily_recap
        # cron should then see the recap_log entry and skip.
        sun_2359 = datetime(2026, 4, 19, 23, 59, tzinfo=LA)
        run_weekly_wrap_early(seeded_repo, SETTINGS, now=sun_2359)
        mock_send.reset_mock()

        mon_0000 = datetime(2026, 4, 20, 0, 0, tzinfo=LA)
        body = run_daily_recap(seeded_repo, SETTINGS, now=mon_0000)
        assert body is None
        mock_send.assert_not_called()


class TestPeriodEndRecapBlocks:
    """``render_daily`` (and the cron) append Month/Year totals when
    the target_day is the last day of the period. Separate from the
    core recap path so the common case doesn't pay for the extra
    repo queries."""

    def test_last_day_of_month_appends_month_totals(self):
        # Seed a full April of scores; close out Apr 30 (Thu).
        repo = InMemoryRepository()
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        for d in (date(2026, 4, 1), date(2026, 4, 15), date(2026, 4, 30)):
            repo.insert_score(
                player_id=alice.id, game="queens",
                puzzle_no=700 + (d - date(2026, 4, 1)).days,
                puzzle_date=d, raw_score=30, share_text="x",
            )
        body, _ = render_daily(repo, SETTINGS, date(2026, 4, 30))
        assert "Month totals — Apr 2026" in body

    def test_non_month_end_does_not_append(self):
        repo = InMemoryRepository()
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=date(2026, 4, 14), raw_score=30, share_text="x",
        )
        body, _ = render_daily(repo, SETTINGS, date(2026, 4, 14))
        assert "Month totals" not in body
        assert "Year totals" not in body

    def test_dec_31_appends_both_month_and_year(self):
        repo = InMemoryRepository()
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=999,
            puzzle_date=date(2026, 12, 31), raw_score=30, share_text="x",
        )
        body, _ = render_daily(repo, SETTINGS, date(2026, 12, 31))
        assert "Month totals — Dec 2026" in body
        assert "Year totals — 2026" in body


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


class TestMorningNudgeContextTriggers:
    """The morning nudge can prepend a contextual riff before the
    rotating opener — leaderboard leader, last day of period, active
    win streak. Tests target the gather + render layer; the cron
    integration tests above cover the wiring."""

    def test_no_triggers_returns_empty_list(self):
        from app.db import Player
        from app.jobs import _gather_nudge_context_triggers

        player = Player(id=1, whatsapp_id="whatsapp:+1", display_name="Alice")
        triggers = _gather_nudge_context_triggers(
            player=player,
            today=date(2026, 4, 14),  # Tue, mid-week
            week_scores=[],
            recent_scores=[],
            enabled_games=frozenset({"queens"}),
        )
        assert triggers == []

    def test_leader_trigger_fires_when_player_is_first(self):
        from app.db import Player, ScoreRow
        from app.jobs import _gather_nudge_context_triggers

        # 5-player Queens round; Alice (id=1) wins.
        week = [
            ScoreRow(1, "Alice",   "queens", 700, date(2026, 4, 13), 10),
            ScoreRow(2, "Bob",     "queens", 700, date(2026, 4, 13), 30),
            ScoreRow(3, "Charlie", "queens", 700, date(2026, 4, 13), 40),
        ]
        alice = Player(id=1, whatsapp_id="whatsapp:+1", display_name="Alice")
        triggers = _gather_nudge_context_triggers(
            player=alice,
            today=date(2026, 4, 14),
            week_scores=week,
            recent_scores=[],
            enabled_games=frozenset({"queens"}),
        )
        kinds = {t.kind for t in triggers}
        assert "leader" in kinds

    def test_leader_trigger_silent_when_player_is_not_first(self):
        from app.db import Player, ScoreRow
        from app.jobs import _gather_nudge_context_triggers

        week = [
            ScoreRow(1, "Alice",   "queens", 700, date(2026, 4, 13), 30),
            ScoreRow(2, "Bob",     "queens", 700, date(2026, 4, 13), 10),
        ]
        alice = Player(id=1, whatsapp_id="whatsapp:+1", display_name="Alice")
        triggers = _gather_nudge_context_triggers(
            player=alice,
            today=date(2026, 4, 14),
            week_scores=week,
            recent_scores=[],
            enabled_games=frozenset({"queens"}),
        )
        kinds = {t.kind for t in triggers}
        assert "leader" not in kinds

    def test_last_day_of_week_fires_on_sunday(self):
        from app.jobs import _detect_last_day_trigger

        sun = date(2026, 4, 19)  # Sunday
        ctx = _detect_last_day_trigger(sun)
        assert ctx is not None
        assert ctx.format_data["period"] == "week"

    def test_last_day_of_month_fires_on_30th_april(self):
        from app.jobs import _detect_last_day_trigger

        ctx = _detect_last_day_trigger(date(2026, 4, 30))
        assert ctx is not None
        # 30 April 2026 is a Thursday — month closes today, week
        # doesn't, so we headline month.
        assert ctx.format_data["period"] == "month"

    def test_last_day_does_not_fire_mid_week_mid_month(self):
        from app.jobs import _detect_last_day_trigger

        # Tue 14 Apr 2026 — nothing closes.
        assert _detect_last_day_trigger(date(2026, 4, 14)) is None

    def test_streak_trigger_fires_after_two_consecutive_wins(self):
        from app.db import Player, ScoreRow
        from app.jobs import _gather_nudge_context_triggers

        alice = Player(id=1, whatsapp_id="whatsapp:+1", display_name="Alice")
        # Alice wins Queens on Mon and Tue; today is Wed.
        recent = [
            ScoreRow(1, "Alice", "queens", 700, date(2026, 4, 13), 10),
            ScoreRow(2, "Bob",   "queens", 700, date(2026, 4, 13), 30),
            ScoreRow(1, "Alice", "queens", 701, date(2026, 4, 14), 10),
            ScoreRow(2, "Bob",   "queens", 701, date(2026, 4, 14), 30),
        ]
        triggers = _gather_nudge_context_triggers(
            player=alice,
            today=date(2026, 4, 15),
            week_scores=[],
            recent_scores=recent,
            enabled_games=frozenset({"queens"}),
        )
        streak_triggers = [t for t in triggers if t.kind == "streak"]
        assert len(streak_triggers) == 1
        assert streak_triggers[0].format_data["streak"] == "2"

    def test_streak_trigger_silent_when_only_one_win(self):
        from app.db import Player, ScoreRow
        from app.jobs import _gather_nudge_context_triggers

        alice = Player(id=1, whatsapp_id="whatsapp:+1", display_name="Alice")
        # Alice won Queens yesterday only — single-day streak doesn't
        # qualify (threshold is 2+ to be worth a callout).
        recent = [
            ScoreRow(1, "Alice", "queens", 700, date(2026, 4, 14), 10),
            ScoreRow(2, "Bob",   "queens", 700, date(2026, 4, 14), 30),
        ]
        triggers = _gather_nudge_context_triggers(
            player=alice,
            today=date(2026, 4, 15),
            week_scores=[],
            recent_scores=recent,
            enabled_games=frozenset({"queens"}),
        )
        kinds = {t.kind for t in triggers}
        assert "streak" not in kinds

    def test_streak_breaks_on_loss(self):
        from app.db import Player, ScoreRow
        from app.jobs import _gather_nudge_context_triggers

        alice = Player(id=1, whatsapp_id="whatsapp:+1", display_name="Alice")
        # Alice won Mon, lost Tue. Today is Wed → streak ended at 0.
        recent = [
            ScoreRow(1, "Alice", "queens", 700, date(2026, 4, 13), 10),
            ScoreRow(2, "Bob",   "queens", 700, date(2026, 4, 13), 30),
            ScoreRow(1, "Alice", "queens", 701, date(2026, 4, 14), 30),
            ScoreRow(2, "Bob",   "queens", 701, date(2026, 4, 14), 10),
        ]
        triggers = _gather_nudge_context_triggers(
            player=alice,
            today=date(2026, 4, 15),
            week_scores=[],
            recent_scores=recent,
            enabled_games=frozenset({"queens"}),
        )
        kinds = {t.kind for t in triggers}
        assert "streak" not in kinds


# ---------------------------------------------------------------------------
# run_pre_reset_warning — escalating nags at 2h / 1h / 30min / 5min before
# the LA midnight rollover
# ---------------------------------------------------------------------------


class TestPreResetWarning:
    """Four crons at 22:00 / 23:00 / 23:30 / 23:55 LA. Same skip rules
    as the morning nudge; tone climbs from heads-up to all-caps panic
    as ``stage`` advances."""

    @pytest.mark.parametrize("stage", PRE_RESET_STAGES)
    @patch("app.jobs.send_dm")
    def test_no_active_players_does_nothing(self, mock_dm, stage):
        repo = InMemoryRepository()
        settings = _make_settings({"queens", "tango"})
        now = datetime(2026, 4, 14, 23, 0, tzinfo=LA)
        warned = run_pre_reset_warning(repo, settings, stage=stage, now=now)
        assert warned == []
        mock_dm.assert_not_called()

    @pytest.mark.parametrize("stage", PRE_RESET_STAGES)
    @patch("app.jobs.send_dm")
    def test_skips_player_already_done(self, mock_dm, stage):
        from app.puzzles import la_date
        repo = InMemoryRepository()
        now = datetime(2026, 4, 14, 23, 0, tzinfo=LA)
        today_la = la_date(now)
        _seed(repo, 1, "Alice", today_la, ["queens", "tango"])
        settings = _make_settings({"queens", "tango"})
        warned = run_pre_reset_warning(repo, settings, stage=stage, now=now)
        assert warned == []
        mock_dm.assert_not_called()

    @pytest.mark.parametrize("stage", PRE_RESET_STAGES)
    @patch("app.jobs.send_dm")
    def test_warns_player_with_outstanding_games(self, mock_dm, stage):
        from app.puzzles import la_date
        mock_dm.return_value = True
        repo = InMemoryRepository()
        now = datetime(2026, 4, 14, 23, 0, tzinfo=LA)
        today_la = la_date(now)
        prev_la = today_la - timedelta(days=1)
        # Bob is active (played yesterday) but hasn't played today.
        _seed(repo, 1, "Bob", prev_la, ["queens"])
        settings = _make_settings({"queens", "tango"})
        warned = run_pre_reset_warning(repo, settings, stage=stage, now=now)
        assert warned == ["whatsapp:+61400000001"]
        body = mock_dm.call_args[0][2]
        assert "Bob" in body
        # Both missing games should be called out by name.
        assert "Queens" in body
        assert "Tango" in body

    @pytest.mark.parametrize("stage", PRE_RESET_STAGES)
    @patch("app.jobs.send_dm")
    def test_skips_opted_out_players(self, mock_dm, stage):
        from app.puzzles import la_date
        repo = InMemoryRepository()
        now = datetime(2026, 4, 14, 23, 0, tzinfo=LA)
        prev_la = la_date(now) - timedelta(days=1)
        bob = repo.get_or_create_player("whatsapp:+61400000001", "Bob")
        repo.insert_score(
            player_id=bob.id, game="queens", puzzle_no=713,
            puzzle_date=prev_la, raw_score=10, share_text="x",
        )
        repo.set_notifications_enabled(bob.id, False)
        settings = _make_settings({"queens", "tango"})
        warned = run_pre_reset_warning(repo, settings, stage=stage, now=now)
        assert warned == []
        mock_dm.assert_not_called()

    @pytest.mark.parametrize("stage", PRE_RESET_STAGES)
    @patch("app.jobs.send_dm")
    def test_lists_only_missing_games(self, mock_dm, stage):
        from app.puzzles import la_date
        mock_dm.return_value = True
        repo = InMemoryRepository()
        now = datetime(2026, 4, 14, 23, 0, tzinfo=LA)
        today_la = la_date(now)
        _seed(repo, 1, "Charlie", today_la, ["queens"])  # tango still owed
        settings = _make_settings({"queens", "tango"})
        run_pre_reset_warning(repo, settings, stage=stage, now=now)
        body = mock_dm.call_args[0][2]
        # Message should name the owed game (Tango) but not the
        # already-completed one (Queens).
        assert "Tango" in body
        assert "Queens" not in body

    def test_unknown_stage_raises(self):
        repo = InMemoryRepository()
        settings = _make_settings({"queens"})
        with pytest.raises(ValueError):
            run_pre_reset_warning(repo, settings, stage="bogus")

    @patch("app.jobs.send_dm")
    def test_each_stage_picks_distinct_copy(self, mock_dm):
        """Sanity: at the same instant, the four stages pull from
        their own template pools so a player at 5min gets harsher copy
        than at 2h. Decoupled from exact wording — checks the bodies
        differ across stages."""
        from app.puzzles import la_date
        mock_dm.return_value = True
        now = datetime(2026, 4, 14, 23, 0, tzinfo=LA)
        bodies: dict = {}
        for stage in PRE_RESET_STAGES:
            mock_dm.reset_mock()
            repo = InMemoryRepository()
            today_la = la_date(now)
            _seed(repo, 1, "Bob", today_la - timedelta(days=1), ["queens"])
            settings = _make_settings({"queens", "tango"})
            run_pre_reset_warning(repo, settings, stage=stage, now=now)
            bodies[stage] = mock_dm.call_args[0][2]
        # All four bodies must be distinct — no accidental sharing of
        # the template tuple between stages.
        assert len(set(bodies.values())) == len(PRE_RESET_STAGES)


# ---------------------------------------------------------------------------
# run_new_games_announcement — group blast at 00:01 LA
# ---------------------------------------------------------------------------


class TestNewGamesAnnouncement:
    """Cron at 00:01 LA. Group-broadcasts the "new games are live, get
    in it" hype message right after the daily recap fires. Doesn't
    care whether the recap was sent — purely a hype follow-up."""

    @patch("app.jobs.send_recap")
    def test_no_active_players_does_nothing(self, mock_recap):
        repo = InMemoryRepository()
        settings = _make_settings({"queens", "tango"})
        now = datetime(2026, 4, 15, 0, 1, tzinfo=LA)
        body = run_new_games_announcement(repo, settings, now=now)
        assert body is None
        mock_recap.assert_not_called()

    @patch("app.jobs.send_recap")
    def test_blasts_group_when_players_active(self, mock_recap):
        from app.puzzles import la_date
        repo = InMemoryRepository()
        now = datetime(2026, 4, 15, 0, 1, tzinfo=LA)
        today_la = la_date(now)
        _seed(repo, 1, "Bob", today_la - timedelta(days=1), ["queens"])
        settings = _make_settings({"queens", "tango"})
        body = run_new_games_announcement(repo, settings, now=now)
        assert body is not None
        # Core hook the user asked for must be in the copy.
        assert "get in it" in body.lower()
        mock_recap.assert_called_once()
        # send_recap signature: (settings, body, *, dm_targets=...)
        call_args = mock_recap.call_args
        assert call_args[0][1] == body
        assert call_args[1]["dm_targets"] == ["whatsapp:+61400000001"]

    @patch("app.jobs.send_recap")
    def test_fires_even_when_everyone_is_done(self, mock_recap):
        """Unlike the nag jobs, the new-games hype fires regardless
        of completion state — it announces the new puzzle drop, not
        the old day's outstanding work."""
        from app.puzzles import la_date
        repo = InMemoryRepository()
        now = datetime(2026, 4, 15, 0, 1, tzinfo=LA)
        today_la = la_date(now)
        _seed(repo, 1, "Alice", today_la, ["queens", "tango"])
        settings = _make_settings({"queens", "tango"})
        body = run_new_games_announcement(repo, settings, now=now)
        assert body is not None
        mock_recap.assert_called_once()

    @patch("app.jobs.send_recap")
    def test_copy_rotates_day_to_day(self, mock_recap):
        """Two consecutive LA days should hit different templates so
        the group doesn't get the exact same blast every morning."""
        from app.puzzles import la_date
        repo = InMemoryRepository()
        settings = _make_settings({"queens"})
        # Seed two players on different days so each LA day has
        # someone in the active window.
        now1 = datetime(2026, 4, 15, 0, 1, tzinfo=LA)
        now2 = datetime(2026, 4, 16, 0, 1, tzinfo=LA)
        _seed(repo, 1, "Alice", la_date(now1) - timedelta(days=1), ["queens"])
        _seed(repo, 2, "Bob", la_date(now2) - timedelta(days=1), ["queens"])
        body1 = run_new_games_announcement(repo, settings, now=now1)
        body2 = run_new_games_announcement(repo, settings, now=now2)
        assert body1 is not None and body2 is not None
        assert body1 != body2


class TestNewGamesContextTriggers:
    """Pass C: the new-games message can prepend a contextual riff
    (last day of period / yesterday's blowout) when relevant. Tests
    target the detection helpers directly."""

    def test_last_day_context_fires_on_sunday(self):
        from app.jobs import _new_games_last_day_context

        # Sunday LA — week closes.
        ctx = _new_games_last_day_context(date(2026, 4, 19))
        assert ctx is not None
        assert "week" in ctx

    def test_last_day_context_silent_midweek(self):
        from app.jobs import _new_games_last_day_context

        assert _new_games_last_day_context(date(2026, 4, 14)) is None

    def test_last_day_context_picks_year_over_month(self):
        from app.jobs import _new_games_last_day_context

        # Dec 31 — year closes (also month closes; year wins).
        ctx = _new_games_last_day_context(date(2026, 12, 31))
        assert ctx is not None
        assert "year" in ctx

    def test_blowout_context_fires_when_winner_smashes_field(self):
        from app.db import ScoreRow
        from app.jobs import _new_games_blowout_context

        # Alice 14s vs Bob 50s on Queens — 36s gap → blowout.
        scores = [
            ScoreRow(1, "Alice", "queens", 700, date(2026, 4, 14), 14),
            ScoreRow(2, "Bob",   "queens", 700, date(2026, 4, 14), 50),
        ]
        ctx = _new_games_blowout_context(scores, frozenset({"queens"}))
        assert ctx is not None
        assert "Alice" in ctx
        assert "Queens" in ctx

    def test_blowout_context_silent_when_field_is_tight(self):
        from app.db import ScoreRow
        from app.jobs import _new_games_blowout_context

        # 12s vs 18s — 6s gap, ratio 1.5x. Below both thresholds.
        scores = [
            ScoreRow(1, "Alice", "queens", 700, date(2026, 4, 14), 12),
            ScoreRow(2, "Bob",   "queens", 700, date(2026, 4, 14), 18),
        ]
        ctx = _new_games_blowout_context(scores, frozenset({"queens"}))
        assert ctx is None

    def test_blowout_context_silent_when_solo_player(self):
        from app.db import ScoreRow
        from app.jobs import _new_games_blowout_context

        scores = [
            ScoreRow(1, "Alice", "queens", 700, date(2026, 4, 14), 10),
        ]
        ctx = _new_games_blowout_context(scores, frozenset({"queens"}))
        assert ctx is None


# ---------------------------------------------------------------------------
# send_champion_loser_dms — Sunday LA top/bottom personal messages
# ---------------------------------------------------------------------------


class TestChampionLoserDMs:
    """On Sun LA only, the weekly wrap fan-out triggers a personal DM
    to the overall winner and overall loser of the week."""

    SUN = date(2026, 4, 19)
    MON = date(2026, 4, 13)
    TUE = date(2026, 4, 14)

    @patch("app.jobs.send_dm")
    def test_silent_on_non_sundays(self, mock_dm):
        repo = InMemoryRepository()
        settings = _make_settings({"queens", "tango"})
        _seed(repo, 1, "Alice", self.TUE, ["queens", "tango"])
        _seed(repo, 2, "Bob",   self.TUE, ["queens", "tango"])
        # Tuesday — not a Sunday, should not fire.
        sent = send_champion_loser_dms(repo, settings, self.TUE)
        assert sent == []
        mock_dm.assert_not_called()

    @patch("app.jobs.send_dm")
    def test_silent_with_solo_player(self, mock_dm):
        # One player alone can't be both champ and loser — skip.
        repo = InMemoryRepository()
        settings = _make_settings({"queens"})
        _seed(repo, 1, "Alice", self.MON, ["queens"])
        sent = send_champion_loser_dms(repo, settings, self.SUN)
        assert sent == []
        mock_dm.assert_not_called()

    @patch("app.jobs.send_dm")
    def test_dms_top_and_bottom_of_leaderboard(self, mock_dm):
        mock_dm.return_value = True
        repo = InMemoryRepository()
        settings = _make_settings({"queens"})
        # Alice wins the week (lower raw_score = more points),
        # Charlie loses it.
        repo.insert_score(
            player_id=repo.get_or_create_player(
                "whatsapp:+61400000001", "Alice"
            ).id,
            game="queens", puzzle_no=713,
            puzzle_date=self.MON, raw_score=10, share_text="x",
        )
        repo.insert_score(
            player_id=repo.get_or_create_player(
                "whatsapp:+61400000002", "Bob"
            ).id,
            game="queens", puzzle_no=713,
            puzzle_date=self.MON, raw_score=20, share_text="x",
        )
        repo.insert_score(
            player_id=repo.get_or_create_player(
                "whatsapp:+61400000003", "Charlie"
            ).id,
            game="queens", puzzle_no=713,
            puzzle_date=self.MON, raw_score=30, share_text="x",
        )
        sent = send_champion_loser_dms(repo, settings, self.SUN)
        # Alice (champion) and Charlie (loser) both get a DM.
        assert set(sent) == {
            "whatsapp:+61400000001",
            "whatsapp:+61400000003",
        }
        bodies = [call.args[2] for call in mock_dm.call_args_list]
        # Champion message mentions Alice.
        assert any("Alice" in b for b in bodies)
        # Loser message mentions Charlie.
        assert any("Charlie" in b for b in bodies)
        # Bob (the middle player) should not receive a DM.
        assert not any("Bob" in b for b in bodies)

    @patch("app.jobs.send_dm")
    def test_skips_opted_out_winner(self, mock_dm):
        # If the champion has notifications off, only the loser gets a DM.
        mock_dm.return_value = True
        repo = InMemoryRepository()
        settings = _make_settings({"queens"})
        alice = repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        bob = repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=713,
            puzzle_date=self.MON, raw_score=10, share_text="x",
        )
        repo.insert_score(
            player_id=bob.id, game="queens", puzzle_no=713,
            puzzle_date=self.MON, raw_score=30, share_text="x",
        )
        repo.set_notifications_enabled(alice.id, False)
        sent = send_champion_loser_dms(repo, settings, self.SUN)
        assert sent == ["whatsapp:+61400000002"]
