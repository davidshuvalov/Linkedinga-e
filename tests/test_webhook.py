"""Tests for ``app.webhook.handle_inbound``.

Uses :class:`InMemoryRepository` rather than mocks so assertions can observe
the actual state the repo ended up in after handling a message.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.db import InMemoryRepository
from app.parsers import format_raw_score
from app.webhook import handle_inbound

SYDNEY = ZoneInfo("Australia/Sydney")
NOW = datetime(2026, 4, 14, 19, 0, tzinfo=SYDNEY)


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


# ---------------------------------------------------------------------------
# format_raw_score
# ---------------------------------------------------------------------------


class TestFormatScore:
    def test_pinpoint_single_guess_singular(self):
        assert format_raw_score("pinpoint", 1) == "1 guess"

    def test_pinpoint_multi_guess_plural(self):
        assert format_raw_score("pinpoint", 3) == "3 guesses"

    def test_time_under_minute(self):
        assert format_raw_score("queens", 45) == "0:45"

    def test_time_over_minute_pads_seconds(self):
        assert format_raw_score("tango", 83) == "1:23"

    def test_time_exact_minute(self):
        assert format_raw_score("zip", 60) == "1:00"


# ---------------------------------------------------------------------------
# handle_inbound
# ---------------------------------------------------------------------------


class TestHandleInbound:
    def test_new_player_first_score_is_stored(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #365 | 1:23",
            profile_name="Alice",
            now=NOW,
        )
        assert "Alice" in reply
        assert "Queens" in reply
        assert "#365" in reply
        assert "1:23" in reply

        assert len(repo.scores) == 1
        row = repo.scores[0]
        assert row["game"] == "queens"
        assert row["puzzle_no"] == 365
        assert row["raw_score"] == 83
        assert row["puzzle_date"] == NOW.date()
        assert row["share_text"] == "Queens #365 | 1:23"

    def test_player_auto_created_with_profile_name(self, repo):
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #1 | 0:30",
            profile_name="Alice",
            now=NOW,
        )
        player = repo._players["whatsapp:+61400000001"]
        assert player.display_name == "Alice"

    def test_missing_profile_name_falls_back_to_whatsapp_id(self, repo):
        handle_inbound(
            repo,
            from_="whatsapp:+61400000002",
            body="Queens #1 | 0:30",
            profile_name="",
            now=NOW,
        )
        player = repo._players["whatsapp:+61400000002"]
        assert player.display_name == "whatsapp:+61400000002"

    def test_player_reused_across_messages(self, repo):
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #1 | 0:30",
            profile_name="Alice",
            now=NOW,
        )
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Tango #1 | 0:45",
            profile_name="Alice",
            now=NOW,
        )
        assert len(repo._players) == 1
        assert len(repo.scores) == 2

    def test_duplicate_submission_shows_existing_score(self, repo):
        first = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #365 | 1:23",
            profile_name="Alice",
            now=NOW,
        )
        second = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #365 | 1:30",
            profile_name="Alice",
            now=NOW,
        )
        assert "Got it" in first
        assert "already" in second.lower()
        assert "Queens #365" in second
        # Shows the EXISTING score (1:23) and the new attempt (1:30)
        assert "1:23" in second
        assert "1:30" in second
        assert "not recorded" in second.lower()
        assert len(repo.scores) == 1

    def test_pinpoint_reply_uses_guess_phrasing(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Pinpoint #200 | 3 guesses",
            profile_name="Alice",
            now=NOW,
        )
        assert "3 guesses" in reply
        assert "Pinpoint #200" in reply
        assert len(repo.scores) == 1
        assert repo.scores[0]["raw_score"] == 3

    def test_gameish_message_that_fails_to_parse_is_logged(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens today was a nightmare lnkd.in/queens",
            profile_name="Alice",
            now=NOW,
        )
        assert "couldn't parse" in reply.lower()
        assert len(repo.unparsed) == 1
        assert repo.unparsed[0]["whatsapp_id"] == "whatsapp:+61400000001"
        assert len(repo.scores) == 0

    def test_unrelated_chatter_is_silent(self, repo):
        # Group-chat hygiene: the bot must not reply with help text on
        # normal chatter, otherwise every "hey" would spam the group.
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="hey what's for dinner",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is None
        assert len(repo.scores) == 0
        assert len(repo.unparsed) == 0

    def test_empty_body_is_silent(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is None
        assert len(repo.scores) == 0

    def test_whitespace_only_body_is_silent(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="   \n  ",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is None
        assert len(repo.scores) == 0

    def test_bare_game_name_mention_is_silent(self, repo):
        # Tightened heuristic: without a #N puzzle number, it's chatter.
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens was brutal today",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is None
        assert len(repo.scores) == 0
        assert len(repo.unparsed) == 0

    def test_puzzle_date_comes_from_now(self, repo):
        custom_now = datetime(2026, 1, 1, 20, 0, tzinfo=SYDNEY)
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #1 | 0:30",
            profile_name="Alice",
            now=custom_now,
        )
        assert repo.scores[0]["puzzle_date"] == custom_now.date()

    def test_two_players_both_submit_same_puzzle(self, repo):
        """Different players CAN both submit the same puzzle — dedup is per-player."""
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #365 | 1:23",
            profile_name="Alice",
            now=NOW,
        )
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000002",
            body="Queens #365 | 1:40",
            profile_name="Bob",
            now=NOW,
        )
        assert "already" not in reply.lower()
        assert len(repo.scores) == 2

    def test_patches_round_trip(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Patches #28 | 0:13 🧶",
            profile_name="Alice",
            now=NOW,
        )
        assert "Patches" in reply
        assert "#28" in reply
        assert "0:13" in reply
        assert len(repo.scores) == 1
        assert repo.scores[0]["game"] == "patches"
        assert repo.scores[0]["puzzle_no"] == 28
        assert repo.scores[0]["raw_score"] == 13

    def test_mini_sudoku_reply_uses_display_name(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Mini Sudoku #246 | 1:16 ✏️",
            profile_name="Alice",
            now=NOW,
        )
        # Display name keeps the space
        assert "Mini Sudoku" in reply
        assert "#246" in reply
        assert "1:16" in reply
        assert len(repo.scores) == 1
        assert repo.scores[0]["game"] == "mini_sudoku"
        assert repo.scores[0]["raw_score"] == 76

    def test_submission_stores_la_anchored_puzzle_date(self, repo):
        # 4:45pm Sydney on 22 Apr 2026 AEST is still 21 Apr in LA — the
        # puzzle_date should record 21 Apr, matching LinkedIn's day.
        before_flip = datetime(2026, 4, 22, 16, 45, tzinfo=SYDNEY)
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #720\n1:05",
            profile_name="Alice",
            now=before_flip,
        )
        from datetime import date

        assert repo.scores[0]["puzzle_date"] == date(2026, 4, 21)

    def test_submission_after_la_flip_stores_new_la_day(self, repo):
        # 5:15pm Sydney AEST on 22 Apr is past midnight LA PDT 22 Apr.
        after_flip = datetime(2026, 4, 22, 17, 15, tzinfo=SYDNEY)
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #721\n1:05",
            profile_name="Alice",
            now=after_flip,
        )
        from datetime import date

        assert repo.scores[0]["puzzle_date"] == date(2026, 4, 22)

    def test_zip_407_today_rejected(self, repo):
        # Belt-and-braces: the Zip #407 case David called out explicitly.
        # With the Zip epoch at 401 for 22 Apr LA, #407 is still a future
        # puzzle and should be rejected.
        from app.puzzles import expected_puzzle_no

        today = datetime(2026, 4, 22, 20, 0, tzinfo=SYDNEY)
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Zip #407\n0:09",
            profile_name="Alice",
            now=today,
            expected_puzzle_no=expected_puzzle_no,
        )
        assert reply is not None
        assert "#407" in reply
        assert "#401" in reply  # today's Zip per the current epoch
        assert len(repo.scores) == 0

    def test_gameish_with_hash_but_unparseable_still_replies(self, repo):
        # A message with both a game name AND a #N looks like a genuine
        # upload attempt — we want the user to know it couldn't parse so
        # they can fix their paste.
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #500 i think i got",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is not None
        assert "couldn't parse" in reply.lower()
        assert len(repo.unparsed) == 1


# ---------------------------------------------------------------------------
# /stats command
# ---------------------------------------------------------------------------


class TestStatsCommand:
    def test_stats_with_no_scores(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="stats",
            profile_name="Alice",
            now=NOW,
        )
        assert "No scores recorded" in reply

    def test_stats_with_scores(self, repo):
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #1 | 0:30",
            profile_name="Alice",
            now=NOW,
        )
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #2 | 0:20",
            profile_name="Alice",
            now=NOW,
        )
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Pinpoint #1 | 2 guesses",
            profile_name="Alice",
            now=NOW,
        )
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="stats",
            profile_name="Alice",
            now=NOW,
        )
        assert "Alice" in reply
        assert "Submissions: 3" in reply
        assert "Games played: 2/7" in reply
        assert "Personal bests:" in reply
        # Best Queens is 0:20 not 0:30
        assert "0:20" in reply
        assert "2 guesses" in reply

    def test_stats_case_insensitive(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="STATS",
            profile_name="Alice",
            now=NOW,
        )
        assert "No scores recorded" in reply


# ---------------------------------------------------------------------------
# /unparsed command
# ---------------------------------------------------------------------------


class TestUnparsedCommand:
    def test_unparsed_with_nothing_logged(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="unparsed",
            profile_name="Alice",
            now=NOW,
        )
        assert "No unparsed messages" in reply

    def test_unparsed_shows_logged_entries(self, repo):
        # Trigger an unparsed log
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens today was a nightmare lnkd.in/queens",
            profile_name="Alice",
            now=NOW,
        )
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="unparsed",
            profile_name="Alice",
            now=NOW,
        )
        assert "Recent unparsed" in reply
        assert "Queens today was a nightmare" in reply

    def test_unparsed_case_insensitive(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Unparsed",
            profile_name="Alice",
            now=NOW,
        )
        assert "No unparsed messages" in reply


# ---------------------------------------------------------------------------
# /recap and /wrap on-demand commands
# ---------------------------------------------------------------------------


def _settings_with_default_games():
    """Settings with a real enabled_games set (needed by recap/wrap)."""
    from app.config import Settings
    return Settings(
        twilio_account_sid="",
        twilio_auth_token="",
        twilio_whatsapp_from="",
        twilio_recap_to="",
        supabase_url="",
        supabase_key="",
        timezone_name="Australia/Sydney",
        enabled_games=frozenset(
            {"queens", "tango", "zip", "patches", "mini_sudoku"}
        ),
    )


class TestRecapCommand:
    def test_recap_with_no_scores_yet(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="recap",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert "Daily recap" in reply
        assert "No scores yet" in reply

    def test_recap_includes_week_so_far(self, repo):
        # Seed two scores on the LA day matching NOW.
        from app.puzzles import la_date

        today_la = la_date(NOW)
        repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        repo.insert_score(
            player_id=1, game="queens", puzzle_no=714,
            puzzle_date=today_la, raw_score=10,
            share_text="Queens #714 0:10",
        )
        repo.insert_score(
            player_id=2, game="queens", puzzle_no=714,
            puzzle_date=today_la, raw_score=20,
            share_text="Queens #714 0:20",
        )

        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="recap",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Queens #714" in reply
        assert "Week so far:" in reply
        assert "1. Alice" in reply

    def test_today_is_alias_for_recap(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="today",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Daily recap" in reply

    def test_recap_case_insensitive(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="RECAP",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Daily recap" in reply


class TestWrapCommand:
    def test_wrap_with_no_scores(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="wrap",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert "Weekly wrap" in reply
        assert "No scores this week" in reply

    def test_week_is_alias_for_wrap(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="week",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Weekly wrap" in reply


class TestHistoricalRecapCommands:
    """``yesterday`` / ``N days ago`` / ``recap YYYY-MM-DD`` — pull
    prior-day recaps so a midweek user can check what happened without
    scrolling back through old DMs."""

    def _seed_day(self, repo, day, player_id, name, raw, puzzle_no=714):
        repo.get_or_create_player(f"whatsapp:+6140000000{player_id}", name)
        repo.insert_score(
            player_id=player_id,
            game="queens",
            puzzle_no=puzzle_no,
            puzzle_date=day,
            raw_score=raw,
            share_text=f"Queens #{puzzle_no} seeded",
        )

    def test_yesterday_pulls_previous_la_day(self, repo):
        from datetime import timedelta
        from app.puzzles import la_date

        today = la_date(NOW)
        yesterday = today - timedelta(days=1)
        self._seed_day(repo, yesterday, 1, "Alice", 10, puzzle_no=713)

        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="yesterday",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert "Daily recap" in reply
        assert yesterday.strftime("%a %d %b") in reply
        assert "Queens #713" in reply

    def test_n_days_ago_pulls_that_day(self, repo):
        from datetime import timedelta
        from app.puzzles import la_date

        today = la_date(NOW)
        three_ago = today - timedelta(days=3)
        self._seed_day(repo, three_ago, 1, "Alice", 15, puzzle_no=711)

        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="3 days ago",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert three_ago.strftime("%a %d %b") in reply
        assert "Queens #711" in reply

    def test_n_days_ago_out_of_range_replies_with_hint(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="30 days ago",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert "last 6 days" in reply or "6 days ago" in reply

    def test_recap_with_iso_date(self, repo):
        from datetime import timedelta
        from app.puzzles import la_date

        today = la_date(NOW)
        two_ago = today - timedelta(days=2)
        self._seed_day(repo, two_ago, 1, "Alice", 12, puzzle_no=712)

        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body=f"recap {two_ago.isoformat()}",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert "Queens #712" in reply

    def test_recap_with_future_date_rejected(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="recap 2099-01-01",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert "future" in reply.lower()


class TestAllWeekCommand:
    def test_all_with_no_scores(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="all",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert "No scores yet this week" in reply

    def test_all_includes_every_day_with_scores(self, repo):
        from datetime import timedelta
        from app.puzzles import la_date, week_bounds

        today = la_date(NOW)
        monday, _ = week_bounds(today)
        repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        # Seed one score per day of the week so far.
        cursor = monday
        pno = 700
        while cursor <= today:
            repo.insert_score(
                player_id=1, game="queens", puzzle_no=pno,
                puzzle_date=cursor, raw_score=10,
                share_text=f"Queens #{pno}",
            )
            cursor += timedelta(days=1)
            pno += 1

        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="all",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert "Week so far" in reply
        # Every weekday header Mon..today appears.
        cursor = monday
        while cursor <= today:
            assert cursor.strftime("%a %d %b") in reply
            cursor += timedelta(days=1)
