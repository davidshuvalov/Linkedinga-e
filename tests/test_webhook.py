"""Tests for ``app.webhook.handle_inbound``.

Uses :class:`InMemoryRepository` rather than mocks so assertions can observe
the actual state the repo ended up in after handling a message.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.db import InMemoryRepository
from app.webhook import _format_score, handle_inbound

SYDNEY = ZoneInfo("Australia/Sydney")
NOW = datetime(2026, 4, 14, 19, 0, tzinfo=SYDNEY)


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


# ---------------------------------------------------------------------------
# _format_score
# ---------------------------------------------------------------------------


class TestFormatScore:
    def test_pinpoint_single_guess_singular(self):
        assert _format_score("pinpoint", 1) == "1 guess"

    def test_pinpoint_multi_guess_plural(self):
        assert _format_score("pinpoint", 3) == "3 guesses"

    def test_time_under_minute(self):
        assert _format_score("queens", 45) == "0:45"

    def test_time_over_minute_pads_seconds(self):
        assert _format_score("tango", 83) == "1:23"

    def test_time_exact_minute(self):
        assert _format_score("zip", 60) == "1:00"


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

    def test_unrelated_chatter_gets_help_text(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="hey what's for dinner",
            profile_name="Alice",
            now=NOW,
        )
        assert len(repo.scores) == 0
        assert len(repo.unparsed) == 0
        assert "LinkedIn" in reply

    def test_empty_body_gets_help_text(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="",
            profile_name="Alice",
            now=NOW,
        )
        assert "LinkedIn" in reply
        assert len(repo.scores) == 0

    def test_whitespace_only_body_gets_help_text(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="   \n  ",
            profile_name="Alice",
            now=NOW,
        )
        assert "LinkedIn" in reply
        assert len(repo.scores) == 0

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

    def test_help_text_lists_all_seven_games(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="",
            profile_name="Alice",
            now=NOW,
        )
        for name in (
            "Queens",
            "Tango",
            "Pinpoint",
            "Crossclimb",
            "Zip",
            "Patches",
            "Mini Sudoku",
        ):
            assert name in reply


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
