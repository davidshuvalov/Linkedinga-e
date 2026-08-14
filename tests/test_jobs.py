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

from .conftest import TestRepo
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
    send_period_champion_loser_dms,
)

SYDNEY = ZoneInfo("Australia/Sydney")
LA = ZoneInfo("America/Los_Angeles")

SETTINGS = Settings(
    twilio_account_sid="ACfake",
    twilio_auth_token="fake_token",
    twilio_whatsapp_from="whatsapp:+14155238886",
    twilio_recap_to="whatsapp:+61400000099",
    twilio_status_callback_url="",
    supabase_url="",
    supabase_key="",
    timezone_name="Australia/Sydney",
    enabled_games=frozenset(
        {"queens", "tango", "zip", "patches", "mini_sudoku"}
    ),
)


@pytest.fixture
def seeded_repo() -> InMemoryRepository:
    repo = TestRepo()
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
        assert mock_send.call_count >= 1

    @patch("app.jobs.send_recap")
    def test_sydney_wallclock_equivalent_also_works(self, mock_send, seeded_repo):
        # 17:00 Sydney AEST on Apr 15 = 00:00 PDT on Apr 15 LA.
        now = datetime(2026, 4, 15, 17, 0, tzinfo=SYDNEY)
        body = run_daily_recap(seeded_repo, SETTINGS, now=now)

        assert "Tue 14 Apr 2026" in body
        assert "Queens #714" in body

    @patch("app.jobs.send_recap")
    def test_empty_day_still_sends(self, mock_send):
        repo = TestRepo()
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
        repo = TestRepo()
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        for d in (date(2026, 4, 1), date(2026, 4, 15), date(2026, 4, 30)):
            repo.insert_score(
                player_id=alice.id, game="queens",
                puzzle_no=700 + (d - date(2026, 4, 1)).days,
                puzzle_date=d, raw_score=30, share_text="x",
            )
        body, _ = render_daily(repo, SETTINGS, date(2026, 4, 30), group_id=repo.default_group.id)
        assert "Month totals — Apr 2026" in body

    def test_non_month_end_does_not_append(self):
        repo = TestRepo()
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=date(2026, 4, 14), raw_score=30, share_text="x",
        )
        body, _ = render_daily(repo, SETTINGS, date(2026, 4, 14), group_id=repo.default_group.id)
        assert "Month totals" not in body
        assert "Year totals" not in body

    def test_dec_31_appends_both_month_and_year(self):
        repo = TestRepo()
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=999,
            puzzle_date=date(2026, 12, 31), raw_score=30, share_text="x",
        )
        body, _ = render_daily(repo, SETTINGS, date(2026, 12, 31), group_id=repo.default_group.id)
        assert "Month totals — Dec 2026" in body
        assert "Year totals — 2026" in body


class TestRenderHelpers:
    """``render_daily`` / ``render_wrap`` are the on-demand entry points
    used by the webhook ``recap`` and ``wrap`` commands."""

    def test_render_daily_for_midweek(self, seeded_repo):
        body, dm_targets = render_daily(
            seeded_repo, SETTINGS, date(2026, 4, 14),
            group_id=seeded_repo.default_group.id,
        )
        assert "Daily recap — Tue 14 Apr 2026" in body
        assert "Week so far" in body
        assert dm_targets  # seeded data has active players this week

    def test_render_daily_for_sunday_yields_wrap(self, seeded_repo):
        # Sunday is the magic day where render_daily switches format.
        body, _ = render_daily(
            seeded_repo, SETTINGS, date(2026, 4, 19),
            group_id=seeded_repo.default_group.id,
        )
        assert "Weekly wrap" in body
        assert "Game winners" in body

    def test_render_wrap_works_midweek(self, seeded_repo):
        # Lets a user pull the wrap snapshot from any day in the week,
        # showing partial leaderboard / prizes for the in-progress week.
        body, _ = render_wrap(
            seeded_repo, SETTINGS, date(2026, 4, 14),
            group_id=seeded_repo.default_group.id,
        )
        assert "Weekly wrap" in body
        assert "Mon 13 Apr to Sun 19 Apr 2026" in body


# ---------------------------------------------------------------------------
# maybe_fire_early_recap
# ---------------------------------------------------------------------------


def _make_settings(games, wait_games=()):
    return Settings(
        twilio_account_sid="ACfake",
        twilio_auth_token="fake_token",
        twilio_whatsapp_from="whatsapp:+14155238886",
        twilio_recap_to="whatsapp:+61400000099",
        twilio_status_callback_url="",
        supabase_url="",
        supabase_key="",
        timezone_name="Australia/Sydney",
        enabled_games=frozenset(games),
        wait_games=frozenset(wait_games),
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
        repo = TestRepo()
        settings = _make_settings({"queens"})
        now = datetime(2026, 4, 15, 18, 0, tzinfo=SYDNEY)
        result = maybe_fire_early_recap(repo, settings, now=now, group_id=repo.default_group.id)
        assert result is None

    @patch("app.jobs.send_recap")
    def test_does_not_fire_when_someone_still_owes_a_game(self, mock_send):
        repo = TestRepo()
        today = date(2026, 4, 14)
        # Alice played both, Bob only played queens — Bob still owes tango.
        _seed(repo, 1, "Alice", today, ["queens", "tango"])
        _seed(repo, 2, "Bob",   today, ["queens"])
        settings = _make_settings({"queens", "tango"})
        now = datetime(2026, 4, 14, 12, 0, tzinfo=LA)
        assert maybe_fire_early_recap(repo, settings, now=now, group_id=repo.default_group.id) is None
        mock_send.assert_not_called()
        assert not repo.has_recap_been_sent(today, "daily")

    @patch("app.jobs.send_recap")
    def test_fires_when_everyone_done_and_marks_sent(self, mock_send):
        repo = TestRepo()
        today = date(2026, 4, 14)
        _seed(repo, 1, "Alice", today, ["queens", "tango"])
        _seed(repo, 2, "Bob",   today, ["queens", "tango"])
        settings = _make_settings({"queens", "tango"})
        now = datetime(2026, 4, 14, 12, 0, tzinfo=LA)

        body = maybe_fire_early_recap(repo, settings, now=now, group_id=repo.default_group.id)
        assert body is not None
        assert "Daily recap — Tue 14 Apr 2026" in body
        mock_send.assert_called_once()
        assert repo.has_recap_been_sent(today, "daily")

    @patch("app.jobs.send_recap")
    def test_does_not_double_fire(self, mock_send):
        repo = TestRepo()
        today = date(2026, 4, 14)
        _seed(repo, 1, "Alice", today, ["queens"])
        settings = _make_settings({"queens"})
        now = datetime(2026, 4, 14, 12, 0, tzinfo=LA)

        first = maybe_fire_early_recap(repo, settings, now=now, group_id=repo.default_group.id)
        second = maybe_fire_early_recap(repo, settings, now=now, group_id=repo.default_group.id)
        assert first is not None
        assert second is None  # already sent
        assert mock_send.call_count == 1

    @patch("app.jobs.send_recap")
    def test_sunday_completion_fires_weekly_wrap(self, mock_send):
        repo = TestRepo()
        sunday = date(2026, 4, 19)
        _seed(repo, 1, "Alice", sunday, ["queens"])
        settings = _make_settings({"queens"})
        now = datetime(2026, 4, 19, 12, 0, tzinfo=LA)

        body = maybe_fire_early_recap(repo, settings, now=now, group_id=repo.default_group.id)
        assert body is not None
        assert "Weekly wrap" in body
        assert repo.has_recap_been_sent(sunday, "weekly")
        # The daily slot for Sunday is *not* marked — they're separate
        # types in recap_log so a Mon–Sat early-fire on a different day
        # wouldn't accidentally suppress this one.
        assert not repo.has_recap_been_sent(sunday, "daily")


class TestEarlyRecapWaitsForUnscoredGames:
    """Regression: the group added Wend to its daily routine but kept
    it off the leaderboard, and the recap fired the moment the five
    scored games landed — while everyone was still playing Wend.

    ``wait_games`` holds the day open without letting the game score.
    """

    @patch("app.jobs.send_recap")
    def test_does_not_fire_while_a_waited_for_game_is_outstanding(
        self, mock_send
    ):
        repo = TestRepo()
        today = date(2026, 4, 14)
        # Both players finished every *scored* game; neither has sent
        # Wend yet. Under the old rule this fired immediately.
        _seed(repo, 1, "Alice", today, ["queens", "tango"])
        _seed(repo, 2, "Bob",   today, ["queens", "tango"])
        settings = _make_settings({"queens", "tango"}, wait_games={"wend"})
        now = datetime(2026, 4, 14, 12, 0, tzinfo=LA)

        assert maybe_fire_early_recap(
            repo, settings, now=now, group_id=repo.default_group.id
        ) is None
        mock_send.assert_not_called()
        assert not repo.has_recap_been_sent(today, "daily")

    @patch("app.jobs.send_recap")
    def test_fires_once_the_last_waited_for_game_lands(self, mock_send):
        repo = TestRepo()
        today = date(2026, 4, 14)
        _seed(repo, 1, "Alice", today, ["queens", "tango", "wend"])
        _seed(repo, 2, "Bob",   today, ["queens", "tango"])
        settings = _make_settings({"queens", "tango"}, wait_games={"wend"})
        now = datetime(2026, 4, 14, 12, 0, tzinfo=LA)

        # Bob still owes Wend.
        assert maybe_fire_early_recap(
            repo, settings, now=now, group_id=repo.default_group.id
        ) is None

        repo.insert_score(
            player_id=2, game="wend", puzzle_no=67,
            puzzle_date=today, raw_score=42, share_text="x",
        )
        body = maybe_fire_early_recap(
            repo, settings, now=now, group_id=repo.default_group.id
        )
        assert body is not None
        mock_send.assert_called_once()
        # Held the day open, but stayed out of the scoring: the recap
        # never names it.
        assert "Wend" not in body

    @patch("app.jobs.send_recap")
    def test_group_override_beats_global_wait_list(self, mock_send):
        # The group ran ``waitfor none`` — an explicit empty override,
        # which must not be mistaken for "inherit the global list".
        repo = TestRepo()
        today = date(2026, 4, 14)
        repo.set_group_wait_games(repo.default_group.id, frozenset())
        _seed(repo, 1, "Alice", today, ["queens"])
        settings = _make_settings({"queens"}, wait_games={"wend"})
        now = datetime(2026, 4, 14, 12, 0, tzinfo=LA)

        body = maybe_fire_early_recap(
            repo, settings, now=now, group_id=repo.default_group.id
        )
        assert body is not None
        mock_send.assert_called_once()


class TestRunDailyRecapSkipsAfterEarlyFire:
    """The cron sees the recap_log and bails out without re-sending."""

    @patch("app.jobs.send_recap")
    def test_cron_skips_when_recap_already_sent(self, mock_send):
        repo = TestRepo()
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
        repo = TestRepo()
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
        repo = TestRepo()
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
        repo = TestRepo()
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
        repo = TestRepo()
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
        repo = TestRepo()
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
        repo = TestRepo()
        settings = _make_settings({"queens", "tango"})
        now = datetime(2026, 4, 14, 23, 0, tzinfo=LA)
        warned = run_pre_reset_warning(repo, settings, stage=stage, now=now)
        assert warned == []
        mock_dm.assert_not_called()

    @pytest.mark.parametrize("stage", PRE_RESET_STAGES)
    @patch("app.jobs.send_dm")
    def test_skips_player_already_done(self, mock_dm, stage):
        from app.puzzles import la_date
        repo = TestRepo()
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
        repo = TestRepo()
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
        repo = TestRepo()
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
        repo = TestRepo()
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
        repo = TestRepo()
        settings = _make_settings({"queens"})
        with pytest.raises(ValueError):
            run_pre_reset_warning(repo, settings, stage="bogus")

    @pytest.mark.parametrize("stage", PRE_RESET_STAGES)
    @patch("app.jobs.send_dm")
    def test_nags_about_an_outstanding_waited_for_game(self, mock_dm, stage):
        # The recap is blocked on this player's Wend — going quiet on
        # them is exactly backwards.
        from app.puzzles import la_date
        mock_dm.return_value = True
        repo = TestRepo()
        now = datetime(2026, 4, 14, 23, 0, tzinfo=LA)
        today_la = la_date(now)
        _seed(repo, 1, "Alice", today_la, ["queens"])
        settings = _make_settings({"queens"}, wait_games={"wend"})
        warned = run_pre_reset_warning(repo, settings, stage=stage, now=now)
        assert warned == ["whatsapp:+61400000001"]
        body = mock_dm.call_args[0][2]
        assert "Wend" in body
        assert "Queens" not in body  # already in

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
            repo = TestRepo()
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
        repo = TestRepo()
        settings = _make_settings({"queens", "tango"})
        now = datetime(2026, 4, 15, 0, 1, tzinfo=LA)
        body = run_new_games_announcement(repo, settings, now=now)
        assert body is None
        mock_recap.assert_not_called()

    @patch("app.jobs.send_recap")
    def test_blasts_group_when_players_active(self, mock_recap):
        from app.puzzles import la_date
        repo = TestRepo()
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
        repo = TestRepo()
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
        repo = TestRepo()
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
        repo = TestRepo()
        settings = _make_settings({"queens", "tango"})
        _seed(repo, 1, "Alice", self.TUE, ["queens", "tango"])
        _seed(repo, 2, "Bob",   self.TUE, ["queens", "tango"])
        # Tuesday — not a Sunday, should not fire.
        sent = send_champion_loser_dms(repo, settings, self.TUE, group_id=repo.default_group.id)
        assert sent == []
        mock_dm.assert_not_called()

    @patch("app.jobs.send_dm")
    def test_silent_with_solo_player(self, mock_dm):
        # One player alone can't be both champ and loser — skip.
        repo = TestRepo()
        settings = _make_settings({"queens"})
        _seed(repo, 1, "Alice", self.MON, ["queens"])
        sent = send_champion_loser_dms(repo, settings, self.SUN, group_id=repo.default_group.id)
        assert sent == []
        mock_dm.assert_not_called()

    @patch("app.jobs.send_dm")
    def test_dms_top_and_bottom_of_leaderboard(self, mock_dm):
        mock_dm.return_value = True
        repo = TestRepo()
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
        sent = send_champion_loser_dms(repo, settings, self.SUN, group_id=repo.default_group.id)
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
        # Bob (the middle player) should not receive a DM (2 DMs total).
        assert len(sent) == 2

    @patch("app.jobs.send_dm")
    def test_skips_opted_out_winner(self, mock_dm):
        # If the champion has notifications off, only the loser gets a DM.
        mock_dm.return_value = True
        repo = TestRepo()
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
        sent = send_champion_loser_dms(repo, settings, self.SUN, group_id=repo.default_group.id)
        assert sent == ["whatsapp:+61400000002"]


# ---------------------------------------------------------------------------
# Multi-group fan-out for the cron entry points
# ---------------------------------------------------------------------------


class TestCronGroupFanOut:
    """The cron entry points loop over every group in
    :meth:`Repository.list_groups` so each group gets its own
    recap / morning nudge. A failure in one group is logged and
    doesn't disturb the others."""

    def _two_group_repo(self, today_la):
        from app.db import InMemoryRepository
        repo = InMemoryRepository()
        a = repo.get_or_create_group("ACrew")
        b = repo.get_or_create_group("BCrew")
        # One queens score per group on the closed LA day so each
        # has something to recap.
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        bob = repo.get_or_create_player("whatsapp:+2", "Bob")
        repo.set_player_group(alice.id, a.id)
        repo.set_player_group(bob.id, b.id)
        repo.insert_score(
            player_id=alice.id, group_id=a.id, game="queens",
            puzzle_no=714, puzzle_date=today_la, raw_score=10,
            share_text="x",
        )
        repo.insert_score(
            player_id=bob.id, group_id=b.id, game="queens",
            puzzle_no=714, puzzle_date=today_la, raw_score=20,
            share_text="x",
        )
        return repo, a, b

    @patch("app.jobs.send_recap")
    def test_run_daily_recap_marks_each_group(self, mock_send):
        # Cron fires at apr 15 00:00 LA → recaps apr 14 (Tue, midweek
        # daily). Both groups should land in recap_log.
        target = date(2026, 4, 14)
        repo, a, b = self._two_group_repo(target)
        run_daily_recap(repo, _make_settings({"queens"}),
                        now=datetime(2026, 4, 15, 0, 0, tzinfo=LA))
        assert repo.has_recap_been_sent(target, "daily", group_id=a.id)
        assert repo.has_recap_been_sent(target, "daily", group_id=b.id)
        # send_recap called once per group.
        assert mock_send.call_count == 2

    @patch("app.jobs.send_recap")
    def test_run_daily_recap_continues_past_per_group_failure(self, mock_send):
        # First call raises, second succeeds. The exception is logged
        # and BCrew still gets its recap marked.
        target = date(2026, 4, 14)
        repo, a, b = self._two_group_repo(target)
        # Inject a failure for ACrew only.
        original = repo.has_recap_been_sent

        def flaky(recap_date, recap_type, *, group_id):
            if group_id == a.id:
                raise RuntimeError("simulated transient failure")
            return original(recap_date, recap_type, group_id=group_id)

        repo.has_recap_been_sent = flaky
        run_daily_recap(repo, _make_settings({"queens"}),
                        now=datetime(2026, 4, 15, 0, 0, tzinfo=LA))
        # ACrew didn't mark; BCrew did.
        assert original(target, "daily", group_id=b.id)
        # Only one successful send.
        assert mock_send.call_count == 1


# ---------------------------------------------------------------------------
# End-of-day recap: fires when all players done OR at the deadline
# ---------------------------------------------------------------------------


class TestRecapDeadlineFire:
    """The recap has two firing paths:
      1. Event-driven early-fire — the moment the last player submits.
      2. Scheduled deadline — 00:00 LA daily (Mon–Sat) or 23:59 LA Sunday.

    Both paths must produce a recap even when not every player has
    submitted (the cron is a hard deadline, not a poll).
    """

    # ---- early-fire path ------------------------------------------------

    @patch("app.jobs.send_recap")
    def test_early_fire_triggers_when_last_player_submits(self, mock_send):
        """maybe_fire_early_recap fires immediately when every active
        player has submitted all enabled games — the 'everyone done'
        path."""
        mock_send.return_value = None
        repo = TestRepo()
        today = date(2026, 4, 14)
        settings = _make_settings({"queens", "tango"})
        now = datetime(2026, 4, 14, 14, 0, tzinfo=LA)

        # After Alice submits both games → she is the only active player → done.
        _seed(repo, 1, "Alice", today, ["queens", "tango"])
        body = maybe_fire_early_recap(repo, settings, now=now,
                                      group_id=repo.default_group.id)
        assert body is not None
        assert "Daily recap" in body or "Weekly wrap" in body
        mock_send.assert_called_once()
        assert repo.has_recap_been_sent(today, "daily")

    @patch("app.jobs.send_recap")
    def test_early_fire_waits_until_all_players_done(self, mock_send):
        """Does NOT fire while at least one active player is still owed
        a game."""
        repo = TestRepo()
        today = date(2026, 4, 14)
        settings = _make_settings({"queens", "tango"})
        now = datetime(2026, 4, 14, 14, 0, tzinfo=LA)

        # Alice is done; Bob only played queens — tango outstanding.
        _seed(repo, 1, "Alice", today, ["queens", "tango"])
        _seed(repo, 2, "Bob",   today, ["queens"])
        result = maybe_fire_early_recap(repo, settings, now=now,
                                        group_id=repo.default_group.id)
        assert result is None
        mock_send.assert_not_called()

    @patch("app.jobs.send_recap")
    def test_early_fire_sends_and_cron_then_skips(self, mock_send):
        """Once early-fired, the midnight cron finds the recap_log entry
        and skips — no double send."""
        mock_send.return_value = None
        repo = TestRepo()
        today = date(2026, 4, 14)  # Tuesday LA
        settings = _make_settings({"queens"})

        _seed(repo, 1, "Alice", today, ["queens"])
        # Early fire succeeds during the day.
        maybe_fire_early_recap(
            repo, settings,
            now=datetime(2026, 4, 14, 15, 0, tzinfo=LA),
            group_id=repo.default_group.id,
        )
        assert mock_send.call_count == 1
        mock_send.reset_mock()

        # Midnight cron fires → should skip.
        run_daily_recap(
            repo, settings,
            now=datetime(2026, 4, 15, 0, 0, tzinfo=LA),
        )
        mock_send.assert_not_called()

    # ---- cron deadline path (Mon–Sat 00:00 LA) --------------------------

    @patch("app.jobs.send_recap")
    def test_cron_fires_at_midnight_la_even_if_not_all_submitted(self, mock_send):
        """The 00:00 LA cron is a hard deadline — it sends the recap
        regardless of whether every player has finished."""
        mock_send.return_value = None
        repo = TestRepo()
        # Alice played; Bob active earlier this week but skipped today.
        mon = date(2026, 4, 13)
        tue = date(2026, 4, 14)
        _seed(repo, 1, "Alice", tue, ["queens"])
        _seed(repo, 2, "Bob",   mon, ["queens"])  # active but skipped Tue
        settings = _make_settings({"queens"})

        now = datetime(2026, 4, 15, 0, 0, tzinfo=LA)  # midnight → recap Tue
        body = run_daily_recap(repo, settings, now=now)

        assert body is not None
        assert "Daily recap — Tue 14 Apr 2026" in body
        mock_send.assert_called_once()
        assert repo.has_recap_been_sent(tue, "daily")

    @patch("app.jobs.send_recap")
    def test_cron_fires_with_zero_submissions(self, mock_send):
        """Midnight cron still sends even when nobody played that day."""
        mock_send.return_value = None
        repo = TestRepo()
        settings = _make_settings({"queens"})
        now = datetime(2026, 4, 15, 0, 0, tzinfo=LA)
        body = run_daily_recap(repo, settings, now=now)

        assert body is not None
        assert "No scores yet" in body
        mock_send.assert_called_once()

    @patch("app.jobs.send_recap")
    def test_cron_deadline_fires_at_5pm_sydney_equivalently(self, mock_send):
        """00:00 LA PDT == 17:00 Sydney AEST. Either clock expression
        produces the same daily recap (verified with two fresh repos)."""
        mock_send.return_value = None
        settings = _make_settings({"queens"})

        def _repo_with_score():
            r = TestRepo()
            alice = r.get_or_create_player("whatsapp:+1", "Alice")
            r.insert_score(
                player_id=alice.id, game="queens", puzzle_no=714,
                puzzle_date=date(2026, 4, 14), raw_score=30, share_text="x",
            )
            return r

        # Expressed in LA timezone.
        body_la = run_daily_recap(
            _repo_with_score(), settings,
            now=datetime(2026, 4, 15, 0, 0, tzinfo=LA),
        )
        # Expressed in Sydney timezone — same instant.
        body_syd = run_daily_recap(
            _repo_with_score(), settings,
            now=datetime(2026, 4, 15, 17, 0, tzinfo=SYDNEY),
        )
        assert "Tue 14 Apr 2026" in body_la
        assert "Tue 14 Apr 2026" in body_syd

    # ---- Sunday 23:59 LA (= Mon 16:59 Sydney = "4:59 PM") deadline -----

    @patch("app.jobs.send_recap")
    def test_sunday_wrap_fires_at_2359_la_even_if_not_all_submitted(self, mock_send):
        """run_weekly_wrap_early fires at 23:59 LA Sunday (4:59 PM
        Monday Sydney) regardless of submission completeness.
        This is the hard deadline for the weekly wrap."""
        mock_send.return_value = None
        repo = TestRepo()
        settings = _make_settings({"queens"})

        # Only Alice played this week; Bob is active but skipped.
        mon = date(2026, 4, 13)
        _seed(repo, 1, "Alice", mon, ["queens"])
        _seed(repo, 2, "Bob",   date(2026, 4, 6), ["queens"])  # prior week, counts as active

        # 23:59 LA Sunday → 16:59 Monday Sydney → "4:59 PM"
        now = datetime(2026, 4, 19, 23, 59, tzinfo=LA)
        body = run_weekly_wrap_early(repo, settings, now=now)

        assert body is not None
        assert "Weekly wrap" in body
        mock_send.assert_called_once()
        assert repo.has_recap_been_sent(date(2026, 4, 19), "weekly")

    @patch("app.jobs.send_recap")
    def test_sunday_wrap_early_and_then_monday_cron_skips(self, mock_send):
        """After the 23:59 LA Sunday early wrap fires, the Mon 00:00 LA
        cron sees the recap_log entry and skips. No double-send."""
        mock_send.return_value = None
        repo = TestRepo()
        settings = _make_settings({"queens"})
        _seed(repo, 1, "Alice", date(2026, 4, 13), ["queens"])

        # Sun 23:59 LA fires the wrap.
        run_weekly_wrap_early(repo, settings,
                              now=datetime(2026, 4, 19, 23, 59, tzinfo=LA))
        assert mock_send.call_count == 1
        mock_send.reset_mock()

        # Mon 00:00 LA cron should skip.
        run_daily_recap(repo, settings,
                        now=datetime(2026, 4, 20, 0, 0, tzinfo=LA))
        mock_send.assert_not_called()

    @patch("app.jobs.send_recap")
    def test_early_fire_on_sunday_becomes_weekly_wrap(self, mock_send):
        """When all players finish on a Sunday, the event-driven early-
        fire produces a weekly wrap — same as if the deadline had fired."""
        mock_send.return_value = None
        repo = TestRepo()
        settings = _make_settings({"queens"})
        sunday = date(2026, 4, 19)
        _seed(repo, 1, "Alice", sunday, ["queens"])

        body = maybe_fire_early_recap(
            repo, settings,
            now=datetime(2026, 4, 19, 10, 0, tzinfo=LA),
            group_id=repo.default_group.id,
        )
        assert body is not None
        assert "Weekly wrap" in body
        # Marked as weekly so the 23:59 early wrap and the Mon 00:00 cron skip.
        assert repo.has_recap_been_sent(sunday, "weekly")
        assert not repo.has_recap_been_sent(sunday, "daily")


# ---------------------------------------------------------------------------
# Period champion / loser DMs (month-end and year-end)
# ---------------------------------------------------------------------------


class TestPeriodChampionLoserDMs:
    """send_period_champion_loser_dms fires personal DMs to the period
    champion and wooden-spoon holder at month or year end."""

    SUN = date(2026, 4, 19)   # last Sunday of April (week end)
    APR30 = date(2026, 4, 30)  # last day of April
    DEC31 = date(2026, 12, 31)  # last day of year

    def _seed_two_players(self, repo, game_day, scores=((10, "Alice"), (30, "Bob"))):
        """Seed two players with one score each on ``game_day``."""
        players = []
        for i, (raw, name) in enumerate(scores):
            p = repo.get_or_create_player(f"whatsapp:+6140000000{i+1}", name)
            repo.insert_score(
                player_id=p.id, game="queens",
                puzzle_no=700 + i,
                puzzle_date=game_day, raw_score=raw, share_text="x",
            )
            players.append(p)
        return players

    @patch("app.jobs.send_dm")
    def test_month_end_dms_champion_and_loser(self, mock_dm):
        """On the last day of a month, both month champion and loser
        receive a personal DM."""
        mock_dm.return_value = True
        repo = TestRepo()
        settings = _make_settings({"queens"})
        alice, bob = self._seed_two_players(
            repo, date(2026, 4, 15),
            scores=[(10, "Alice"), (30, "Bob")],
        )
        sent = send_period_champion_loser_dms(
            repo, settings, self.APR30, "month",
            group_id=repo.default_group.id,
        )
        assert len(sent) == 2
        bodies = [call.args[2] for call in mock_dm.call_args_list]
        # Month label must appear in both messages.
        assert all("Apr 2026" in b for b in bodies)
        # Champion message mentions Alice; loser message mentions Bob.
        assert any("Alice" in b for b in bodies)
        assert any("Bob" in b for b in bodies)

    @patch("app.jobs.send_dm")
    def test_year_end_dms_champion_and_loser(self, mock_dm):
        """On Dec 31, year DMs fire with the year label."""
        mock_dm.return_value = True
        repo = TestRepo()
        settings = _make_settings({"queens"})
        self._seed_two_players(
            repo, date(2026, 6, 15),
            scores=[(10, "Alice"), (30, "Bob")],
        )
        sent = send_period_champion_loser_dms(
            repo, settings, self.DEC31, "year",
            group_id=repo.default_group.id,
        )
        assert len(sent) == 2
        bodies = [call.args[2] for call in mock_dm.call_args_list]
        assert all("2026" in b for b in bodies)

    @patch("app.jobs.send_dm")
    def test_month_dms_skipped_for_solo_player(self, mock_dm):
        """A single-player group has no meaningful champion vs loser —
        no DMs sent."""
        repo = TestRepo()
        settings = _make_settings({"queens"})
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=700,
            puzzle_date=date(2026, 4, 15), raw_score=10, share_text="x",
        )
        sent = send_period_champion_loser_dms(
            repo, settings, self.APR30, "month",
            group_id=repo.default_group.id,
        )
        assert sent == []
        mock_dm.assert_not_called()

    @patch("app.jobs.send_dm")
    def test_month_dms_skipped_for_opted_out_players(self, mock_dm):
        """Players with notifications disabled don't receive period DMs."""
        mock_dm.return_value = True
        repo = TestRepo()
        settings = _make_settings({"queens"})
        alice, bob = self._seed_two_players(
            repo, date(2026, 4, 15),
            scores=[(10, "Alice"), (30, "Bob")],
        )
        repo.set_notifications_enabled(alice.id, False)
        repo.set_notifications_enabled(bob.id, False)
        sent = send_period_champion_loser_dms(
            repo, settings, self.APR30, "month",
            group_id=repo.default_group.id,
        )
        assert sent == []

    @patch("app.jobs.send_dm")
    def test_runner_up_name_appears_in_champion_message(self, mock_dm):
        """The champion's DM now names the runner-up (the person they
        beat) so templates can reference them."""
        mock_dm.return_value = True
        repo = TestRepo()
        settings = _make_settings({"queens"})
        # Three players: Alice wins, Bob 2nd, Charlie last.
        for raw, name, wid in [
            (10, "Alice",   "whatsapp:+1"),
            (20, "Bob",     "whatsapp:+2"),
            (30, "Charlie", "whatsapp:+3"),
        ]:
            p = repo.get_or_create_player(wid, name)
            repo.insert_score(
                player_id=p.id, game="queens", puzzle_no=700,
                puzzle_date=date(2026, 4, 15), raw_score=raw, share_text="x",
            )
        sent = send_period_champion_loser_dms(
            repo, settings, self.APR30, "month",
            group_id=repo.default_group.id,
        )
        assert len(sent) == 2  # champion + loser
        champion_body = mock_dm.call_args_list[0].args[2]
        loser_body    = mock_dm.call_args_list[1].args[2]
        # Champion message may reference runner-up (Bob) or loser (Charlie).
        assert "Alice" in champion_body
        # Loser message should reference Charlie.
        assert "Charlie" in loser_body

    @patch("app.jobs.send_recap")
    @patch("app.jobs.send_dm")
    def test_cron_sends_month_dms_on_last_day_of_month(self, mock_dm, mock_recap):
        """The daily recap cron auto-fires month DMs on the last day of
        any month (wired into _run_daily_recap_for_group)."""
        mock_dm.return_value = True
        mock_recap.return_value = None
        repo = TestRepo()
        settings = _make_settings({"queens"})
        # Seed two players on a day in April.
        for raw, name, wid in [(10, "Alice", "whatsapp:+1"), (30, "Bob", "whatsapp:+2")]:
            p = repo.get_or_create_player(wid, name)
            repo.insert_score(
                player_id=p.id, game="queens", puzzle_no=714,
                puzzle_date=date(2026, 4, 14), raw_score=raw, share_text="x",
            )
        # Cron at May 1 00:00 LA → target_day = Apr 30 (last day of month).
        run_daily_recap(repo, settings,
                        now=datetime(2026, 5, 1, 0, 0, tzinfo=LA))
        dm_bodies = [c.args[2] for c in mock_dm.call_args_list]
        # Month DMs should contain "Apr 2026".
        assert any("Apr 2026" in b for b in dm_bodies)

    @patch("app.jobs.send_recap")
    @patch("app.jobs.send_dm")
    def test_cron_sends_year_dms_on_dec_31(self, mock_dm, mock_recap):
        """The cron auto-fires year DMs (not month DMs) on Dec 31."""
        mock_dm.return_value = True
        mock_recap.return_value = None
        repo = TestRepo()
        settings = _make_settings({"queens"})
        for raw, name, wid in [(10, "Alice", "whatsapp:+1"), (30, "Bob", "whatsapp:+2")]:
            p = repo.get_or_create_player(wid, name)
            repo.insert_score(
                player_id=p.id, game="queens", puzzle_no=999,
                puzzle_date=date(2026, 12, 15), raw_score=raw, share_text="x",
            )
        # Cron at Jan 1 2027 00:00 LA → target_day = Dec 31 2026.
        run_daily_recap(repo, settings,
                        now=datetime(2027, 1, 1, 0, 0, tzinfo=LA))
        dm_bodies = [c.args[2] for c in mock_dm.call_args_list]
        # Year DMs contain "2026".
        assert any("2026" in b for b in dm_bodies)
        # Year DMs do NOT contain "Dec 2026" (month template label).
        assert not any("Dec 2026" in b for b in dm_bodies)

    def test_invalid_period_raises(self):
        repo = TestRepo()
        settings = _make_settings({"queens"})
        import pytest
        with pytest.raises(ValueError, match="period must be"):
            send_period_champion_loser_dms(
                repo, settings, self.APR30, "quarter",
                group_id=repo.default_group.id,
            )
