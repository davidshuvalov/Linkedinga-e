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
        assert "couldn't read" in reply.lower()
        # Format hint should be included so the sender knows how to retry.
        assert "Queens #" in reply
        assert "0:10" in reply
        assert len(repo.unparsed) == 1
        assert repo.unparsed[0]["whatsapp_id"] == "whatsapp:+61400000001"
        assert len(repo.scores) == 0

    def test_unrelated_chatter_gets_help(self, repo):
        # Bot operates in 1:1 DMs — silence left users guessing. Now
        # replies with a "didn't understand" blurb + command list so
        # they can see their options.
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="hey what's for dinner",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is not None
        assert "didn't understand" in reply.lower()
        assert "stats" in reply
        assert "recap" in reply
        assert len(repo.scores) == 0
        assert len(repo.unparsed) == 0

    def test_empty_body_is_silent(self, repo):
        # Empty / whitespace sends are accidental (typing indicators,
        # attachment-only messages, etc) — still silent to avoid
        # annoying ping-backs.
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

    def test_bare_game_name_mention_gets_help(self, repo):
        # A bare "Queens was brutal" doesn't have a #N marker so it
        # doesn't look score-ish. Previously silent; now surfaces the
        # help blurb so users aren't left guessing.
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens was brutal today",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is not None
        assert "didn't understand" in reply.lower()
        assert len(repo.scores) == 0
        assert len(repo.unparsed) == 0


class TestHelpCommand:
    def test_help_lists_commands(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="help",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is not None
        for keyword in ("stats", "recap", "yesterday", "week", "all"):
            assert keyword in reply
        # Format hint should appear too so users see how to submit.
        assert "Queens #" in reply

    def test_question_mark_is_alias_for_help(self, repo):
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="?",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is not None
        assert "stats" in reply


# ---------------------------------------------------------------------------
# Utility commands added in the "do it all" batch
# ---------------------------------------------------------------------------


def _seed_three_queens(repo, today):
    """Alice/Bob/Charlie play Queens today with increasing times."""
    for i, (name, raw) in enumerate(
        [("Alice", 10), ("Bob", 20), ("Charlie", 30)], start=1
    ):
        repo.get_or_create_player(f"whatsapp:+6140000000{i}", name)
        repo.insert_score(
            player_id=i, game="queens", puzzle_no=714,
            puzzle_date=today, raw_score=raw,
            share_text=f"Queens #714 {raw}s",
        )


class TestLeaderboardCommand:
    def test_leaderboard_with_no_scores(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="leaderboard",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "No scores yet" in reply

    def test_leaderboard_lists_players_ranked(self, repo):
        from app.puzzles import la_date
        _seed_three_queens(repo, la_date(NOW))
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="leaderboard",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "1. Alice" in reply
        assert "2. Bob" in reply
        assert "3. Charlie" in reply

    def test_leaderboard_per_game_filter(self, repo):
        from app.puzzles import la_date
        _seed_three_queens(repo, la_date(NOW))
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="leaderboard queens",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Queens — week so far" in reply
        assert "Alice" in reply

    def test_leaderboard_unknown_game_falls_through_to_help(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="leaderboard widgets",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        # "leaderboard widgets" isn't a known command; treated as chatter.
        assert "didn't understand" in reply.lower()

    def test_leaderboard_with_iso_date(self, repo):
        from datetime import timedelta
        from app.puzzles import la_date
        today = la_date(NOW)
        two_ago = today - timedelta(days=2)
        repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        repo.insert_score(
            player_id=1, game="queens", puzzle_no=712,
            puzzle_date=two_ago, raw_score=10, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001",
            body=f"leaderboard {two_ago.isoformat()}",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Week so far" in reply
        assert "1. Alice" in reply


class TestPeriodLeaderboards:
    """``month`` / ``mtd`` and ``year`` / ``ytd`` — month-to-date and
    year-to-date leaderboards. Aggregate across the period up through
    the current LA day, rendered with the weekly formatter so the
    shape matches the other leaderboards."""

    def test_month_to_date_aggregates_month(self, repo):
        from datetime import date as _date, timedelta
        # NOW is Tue Apr 14 2026 Sydney (→ LA day 14 Apr). Seed
        # scores earlier in April and one in March so we can see
        # the filter in action.
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        bob   = repo.get_or_create_player("whatsapp:+2", "Bob")
        # March (prior month) — should NOT appear
        repo.insert_score(player_id=alice.id, game="queens", puzzle_no=700,
                          puzzle_date=_date(2026, 3, 30), raw_score=10, share_text="x")
        # April
        repo.insert_score(player_id=alice.id, game="queens", puzzle_no=710,
                          puzzle_date=_date(2026, 4, 1), raw_score=20, share_text="x")
        repo.insert_score(player_id=bob.id,   game="queens", puzzle_no=710,
                          puzzle_date=_date(2026, 4, 1), raw_score=40, share_text="x")
        repo.insert_score(player_id=alice.id, game="queens", puzzle_no=714,
                          puzzle_date=_date(2026, 4, 14), raw_score=30, share_text="x")
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="mtd",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Month so far" in reply
        assert "Apr 2026" in reply
        assert "1. Alice" in reply
        assert "2. Bob" in reply
        # Alice: 2 April rows (Apr 1 + Apr 14); her March row is excluded.
        # Bob: 1 April row.
        assert "G:2" in reply
        assert "G:1" in reply

    def test_year_to_date_aggregates_year(self, repo):
        from datetime import date as _date
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        # Prior year — should NOT appear
        repo.insert_score(player_id=alice.id, game="queens", puzzle_no=600,
                          puzzle_date=_date(2025, 12, 31), raw_score=10, share_text="x")
        # Current year
        repo.insert_score(player_id=alice.id, game="queens", puzzle_no=601,
                          puzzle_date=_date(2026, 1, 2), raw_score=20, share_text="x")
        repo.insert_score(player_id=alice.id, game="queens", puzzle_no=714,
                          puzzle_date=_date(2026, 4, 14), raw_score=30, share_text="x")
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="ytd",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Year so far" in reply
        assert "2026" in reply
        # Two 2026 rows; 2025 row should be filtered out.
        assert "G:2" in reply

    def test_month_with_no_scores(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="month",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "No scores yet" in reply

    def test_month_to_date_aliases(self, repo):
        # "month", "mtd", "month to date", "this month" all dispatch
        # to the same handler.
        from datetime import date as _date
        repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.insert_score(
            player_id=1, game="queens", puzzle_no=714,
            puzzle_date=_date(2026, 4, 14), raw_score=30, share_text="x",
        )
        for keyword in ("month", "mtd", "month to date", "this month"):
            reply = handle_inbound(
                repo, from_="whatsapp:+1", body=keyword,
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
            assert "Month so far" in reply, f"keyword={keyword!r} failed"

    def test_month_includes_prizes_and_game_winners(self, repo):
        # Enough submissions across multiple days to trigger the
        # Best average and Most firsts / Most lasts prizes.
        from datetime import date as _date
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        bob = repo.get_or_create_player("whatsapp:+2", "Bob")
        for i, d in enumerate((
            _date(2026, 4, 1), _date(2026, 4, 5), _date(2026, 4, 10),
            _date(2026, 4, 12), _date(2026, 4, 14),
        )):
            repo.insert_score(
                player_id=alice.id, game="queens", puzzle_no=700 + i,
                puzzle_date=d, raw_score=20, share_text="x",
            )
            repo.insert_score(
                player_id=bob.id, game="queens", puzzle_no=700 + i,
                puzzle_date=d, raw_score=40, share_text="x",
            )
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="month",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        # Full summary shape: leaderboard + game winners + prizes.
        assert "Month so far" in reply
        assert "Game winners:" in reply
        assert "Queens:" in reply
        assert "Prizes:" in reply
        # Alice swept so she should lead Most firsts.
        assert "Most firsts: Alice" in reply

    def test_month_with_game_filter(self, repo):
        from datetime import date as _date
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        bob = repo.get_or_create_player("whatsapp:+2", "Bob")
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=710,
            puzzle_date=_date(2026, 4, 1), raw_score=20, share_text="x",
        )
        repo.insert_score(
            player_id=bob.id, game="queens", puzzle_no=710,
            puzzle_date=_date(2026, 4, 1), raw_score=40, share_text="x",
        )
        # Tango submission should NOT appear in "month queens" output.
        repo.insert_score(
            player_id=alice.id, game="tango", puzzle_no=554,
            puzzle_date=_date(2026, 4, 14), raw_score=25, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="month queens",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Queens — month so far" in reply
        assert "Alice" in reply
        assert "Bob" in reply
        # No Tango row or Prizes block — game-filtered view.
        assert "Tango" not in reply
        assert "Prizes:" not in reply
        assert "Game winners:" not in reply

    def test_year_with_game_filter(self, repo):
        from datetime import date as _date
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=600,
            puzzle_date=_date(2026, 1, 10), raw_score=20, share_text="x",
        )
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=_date(2026, 4, 14), raw_score=25, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="year queens",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Queens — year so far" in reply
        assert "Alice" in reply
        # Two rows in the year; cumulative time = 45s.
        assert "T: 0:45" in reply


class TestTimesCommand:
    def test_times_lists_per_game_ranking_by_time(self, repo):
        from app.puzzles import la_date
        today = la_date(NOW)
        alice = repo.get_or_create_player("whatsapp:+6140000001", "Alice")
        bob = repo.get_or_create_player("whatsapp:+6140000002", "Bob")
        # Queens: Alice 30s, Bob 40s
        repo.insert_score(player_id=alice.id, game="queens", puzzle_no=714,
                          puzzle_date=today, raw_score=30, share_text="x")
        repo.insert_score(player_id=bob.id,   game="queens", puzzle_no=714,
                          puzzle_date=today, raw_score=40, share_text="x")
        # Tango: Bob 20s, Alice 25s
        repo.insert_score(player_id=alice.id, game="tango", puzzle_no=554,
                          puzzle_date=today, raw_score=25, share_text="x")
        repo.insert_score(player_id=bob.id,   game="tango", puzzle_no=554,
                          puzzle_date=today, raw_score=20, share_text="x")
        reply = handle_inbound(
            repo, from_="whatsapp:+6140000001", body="times",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Game times" in reply
        assert "Queens:" in reply
        assert "Tango:" in reply
        # Queens ascending: Alice first (0:30), Bob second (0:40).
        # Tango ascending: Bob first (0:20), Alice second (0:25).
        queens_block = reply.split("Queens:")[1].split("Tango:")[0]
        tango_block = reply.split("Tango:")[1]
        assert "1. Alice" in queens_block
        assert "2. Bob" in queens_block
        assert "1. Bob" in tango_block
        assert "2. Alice" in tango_block

    def test_times_skips_pinpoint(self, repo):
        from app.puzzles import la_date
        today = la_date(NOW)
        repo.get_or_create_player("whatsapp:+6140000001", "Alice")
        repo.insert_score(
            player_id=1, game="pinpoint", puzzle_no=714,
            puzzle_date=today, raw_score=3, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+6140000001", body="times",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        # Pinpoint stores guess counts, not seconds; times command skips it.
        assert "Pinpoint" not in reply

    def test_times_with_no_scores(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+6140000001", body="times",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "No scores" in reply


class TestMissingCommand:
    def test_missing_when_full_turnout(self, repo):
        from app.puzzles import la_date
        _seed_three_queens(repo, la_date(NOW))
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="missing",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "full turnout" in reply.lower()

    def test_missing_names_ghosting_players(self, repo):
        from datetime import timedelta
        from app.puzzles import la_date
        today = la_date(NOW)
        yesterday = today - timedelta(days=1)
        # Alice played yesterday, Bob played today. Alice is "missing".
        repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        repo.insert_score(
            player_id=1, game="queens", puzzle_no=713,
            puzzle_date=yesterday, raw_score=15, share_text="x",
        )
        repo.insert_score(
            player_id=2, game="queens", puzzle_no=714,
            puzzle_date=today, raw_score=20, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000002", body="missing",
            profile_name="Bob", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Alice" in reply
        assert "Still to play today" in reply


class TestPbCommand:
    def test_pb_with_no_scores(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="pb",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "No scores" in reply

    def test_pb_shows_best_per_game(self, repo):
        # Two Queens submissions; PB is the lower raw_score.
        repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        repo.insert_score(
            player_id=1, game="queens", puzzle_no=714,
            puzzle_date=NOW.date(), raw_score=30, share_text="x",
        )
        repo.insert_score(
            player_id=1, game="queens", puzzle_no=715,
            puzzle_date=NOW.date(), raw_score=10, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="pb",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Personal bests for Alice" in reply
        assert "0:10" in reply
        assert "0:30" not in reply


class TestGamesCommand:
    def test_games_lists_enabled_and_disabled(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="games",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Tracked games" in reply
        assert "Queens" in reply
        # Default enabled_games omits Pinpoint + Crossclimb.
        assert "Pinpoint" in reply
        assert "Crossclimb" in reply


class TestRulesCommand:
    def test_rules_mentions_core_concepts(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="rules",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        for keyword in ("competitive", "Base points", "legacy", "Prizes"):
            assert keyword.lower() in reply.lower()

    def test_scoring_is_alias_for_rules(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="scoring",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "legacy" in reply.lower()


class TestPrizesCommand:
    def test_prizes_with_no_scores(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="prizes",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "No scores yet" in reply

    def test_prizes_shows_live_winners(self, repo):
        from app.puzzles import la_date
        _seed_three_queens(repo, la_date(NOW))
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="prizes",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Prizes so far" in reply
        # Alice wins Queens → most firsts; Charlie gets most lasts.
        assert "Alice" in reply


class TestStreakCommand:
    def test_streak_with_no_scores(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="streak",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "no streak yet" in reply.lower()

    def test_streak_counts_consecutive_days(self, repo):
        from datetime import timedelta
        from app.puzzles import la_date
        today = la_date(NOW)
        repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        # 3 consecutive days including today.
        for n in range(3):
            repo.insert_score(
                player_id=1, game="queens", puzzle_no=714 - n,
                puzzle_date=today - timedelta(days=n), raw_score=10,
                share_text="x",
            )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="streak",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "3-day" in reply or "3-days" in reply


class TestVsCommand:
    def test_vs_with_no_shared_rounds(self, repo):
        # Alice has scores, Bob has scores, but on different puzzles.
        repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        repo.insert_score(
            player_id=1, game="queens", puzzle_no=714,
            puzzle_date=NOW.date(), raw_score=10, share_text="x",
        )
        repo.insert_score(
            player_id=2, game="queens", puzzle_no=715,
            puzzle_date=NOW.date(), raw_score=20, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="vs Bob",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "No shared rounds" in reply

    def test_vs_unknown_opponent(self, repo):
        repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        repo.insert_score(
            player_id=1, game="queens", puzzle_no=714,
            puzzle_date=NOW.date(), raw_score=10, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="vs Mystery",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Couldn't find" in reply

    def test_vs_self_rejected(self, repo):
        repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        repo.insert_score(
            player_id=1, game="queens", puzzle_no=714,
            puzzle_date=NOW.date(), raw_score=10, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="vs Alice",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "yourself" in reply.lower()

    def test_vs_reports_head_to_head(self, repo):
        # Alice and Bob both play Queens #714 (Alice wins) and
        # Tango #554 (Bob wins). Expect 1W-1L overall.
        repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        for pid, game, raw in [
            (1, "queens", 10), (2, "queens", 20),
            (1, "tango",  40), (2, "tango",  30),
        ]:
            puzzle_no = 714 if game == "queens" else 554
            repo.insert_score(
                player_id=pid, game=game, puzzle_no=puzzle_no,
                puzzle_date=NOW.date(), raw_score=raw, share_text="x",
            )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="vs Bob",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Alice vs Bob" in reply
        assert "Queens: 1W–0L" in reply
        assert "Tango: 0W–1L" in reply
        assert "Overall: 1W–1L" in reply


class TestUndoCommand:
    def test_undo_with_nothing_today(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="undo",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Nothing to undo" in reply

    def test_undo_removes_todays_submission(self, repo):
        repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        repo.insert_score(
            player_id=1, game="queens", puzzle_no=714,
            puzzle_date=NOW.date(), raw_score=10, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="undo",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Deleted" in reply
        assert len(repo.scores) == 0

    def test_undo_wont_touch_prior_days(self, repo):
        from datetime import timedelta
        from app.puzzles import la_date
        yesterday = la_date(NOW) - timedelta(days=1)
        repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        repo.insert_score(
            player_id=1, game="queens", puzzle_no=713,
            puzzle_date=yesterday, raw_score=10, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="undo",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Nothing to undo" in reply
        assert len(repo.scores) == 1  # yesterday's row survives


class TestNameCommand:
    def test_name_updates_display_name(self, repo):
        player = repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        assert player.display_name == "Alice"
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="name Alicia",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Alicia" in reply
        # Check the rename stuck — second lookup should return the new name.
        # Note: get_or_create_player ignores the passed display_name when a
        # row exists, so the profile_name="Alice" doesn't overwrite.
        updated = repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        assert updated.display_name == "Alicia"

    def test_name_rejects_empty(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="name   ",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        # Trailing whitespace-only name: the parser extracts "" which
        # means the regex didn't match, so it falls through to help.
        assert "didn't understand" in reply.lower() or "empty" in reply.lower()

    def test_name_rejects_overlong(self, repo):
        long_name = "A" * 50
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body=f"name {long_name}",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "too long" in reply.lower()


class TestNotifyCommand:
    def test_notify_off_flips_flag(self, repo):
        player = repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        assert player.notifications_enabled is True
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="notify off",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "off" in reply.lower()
        updated = repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        assert updated.notifications_enabled is False

    def test_notify_on_flips_flag_back(self, repo):
        repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        handle_inbound(
            repo, from_="whatsapp:+61400000001", body="notify off",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="notify on",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "on" in reply.lower()
        updated = repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        assert updated.notifications_enabled is True

    def test_opted_out_player_excluded_from_active_whatsapp_ids(self, repo):
        from datetime import date as _d
        alice = repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        bob = repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=NOW.date(), raw_score=10, share_text="x",
        )
        repo.insert_score(
            player_id=bob.id, game="queens", puzzle_no=714,
            puzzle_date=NOW.date(), raw_score=20, share_text="x",
        )
        repo.set_notifications_enabled(alice.id, False)
        active = repo.list_active_whatsapp_ids(
            date_from=_d(2000, 1, 1), date_to=_d(2100, 1, 1),
        )
        assert alice.whatsapp_id not in active
        assert bob.whatsapp_id in active

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
        assert "couldn't read" in reply.lower()
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
    """Date-scoped queries. Bare date keywords (``yesterday``, ``N days
    ago``) return the full daily recap as of that day — the common ask
    is "what happened yesterday?". The compact leaderboard view is
    still available behind the explicit ``leaderboard …`` /
    ``standings …`` prefix for callers who want just the standings.

    Past-day recaps drop the "Haven't heard from X today" nag at the
    bottom — that footer only makes sense for the current in-progress
    day."""

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

    def test_yesterday_returns_full_recap_for_yesterday(self, repo):
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
        # Full recap shape: per-game header AND the running leaderboard.
        assert "Daily recap" in reply
        assert yesterday.strftime("%a %d %b") in reply
        assert "Queens #713" in reply
        assert "Week so far" in reply
        assert "1. Alice" in reply

    def test_n_days_ago_returns_full_recap_for_that_day(self, repo):
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
        assert "Daily recap" in reply
        assert three_ago.strftime("%a %d %b") in reply
        assert "Queens #711" in reply
        assert "1. Alice" in reply

    def test_past_day_recap_omits_missing_today_nag(self, repo):
        # Two players, only Bob played the day we're asking about.
        # Today's Bob-played-but-Alice-skipped recap would normally
        # nag about Alice. Past-day recaps must drop that footer.
        from datetime import timedelta
        from app.puzzles import la_date

        today = la_date(NOW)
        yesterday = today - timedelta(days=1)
        two_days_ago = today - timedelta(days=2)
        # Alice played 2 days ago, Bob played yesterday → Alice is
        # "missing" for yesterday's recap under the live-day rules.
        self._seed_day(repo, two_days_ago, 1, "Alice", 10, puzzle_no=712)
        self._seed_day(repo, yesterday, 2, "Bob", 12, puzzle_no=713)

        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001",
            body="yesterday",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        # The body of the recap is intact — Bob is in the leaderboard.
        assert "Daily recap" in reply
        assert "Bob" in reply
        # The passive-aggressive "missing today" line MUST be absent.
        for phrase in (
            "MIA today",
            "Still MIA",
            "no-shows",
            "Haven't heard",
            "Where art thou",
            "Benched today",
            "ghosted today's drop",
            "wall of shame",
            "abstainer",
        ):
            assert phrase not in reply

    def test_today_recap_keeps_missing_today_nag(self, repo):
        # Counter-test: when the recap IS for today, the nag returns.
        from datetime import timedelta
        from app.puzzles import la_date

        today = la_date(NOW)
        yesterday = today - timedelta(days=1)
        # Alice played yesterday but skipped today → nag fires.
        self._seed_day(repo, yesterday, 1, "Alice", 10, puzzle_no=713)
        self._seed_day(repo, today, 2, "Bob", 12, puzzle_no=714)

        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001",
            body="recap",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        # Alice's name appears somewhere in the bottom nag block.
        assert "Alice" in reply.split("Week so far")[-1]

    def test_recap_yesterday_returns_full_recap(self, repo):
        # Explicit ``recap yesterday`` still yields the per-game
        # breakdown — different command, different intent.
        from datetime import timedelta
        from app.puzzles import la_date

        today = la_date(NOW)
        yesterday = today - timedelta(days=1)
        self._seed_day(repo, yesterday, 1, "Alice", 10, puzzle_no=713)

        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="recap yesterday",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert "Daily recap" in reply
        assert "Queens #713" in reply

    def test_recap_n_days_ago_returns_full_recap(self, repo):
        from datetime import timedelta
        from app.puzzles import la_date

        today = la_date(NOW)
        three_ago = today - timedelta(days=3)
        self._seed_day(repo, three_ago, 1, "Alice", 15, puzzle_no=711)

        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="recap 3 days ago",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert "Daily recap" in reply
        assert "Queens #711" in reply

    def test_leaderboard_yesterday_alias_works(self, repo):
        from datetime import timedelta
        from app.puzzles import la_date

        today = la_date(NOW)
        yesterday = today - timedelta(days=1)
        self._seed_day(repo, yesterday, 1, "Alice", 10, puzzle_no=713)

        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="leaderboard yesterday",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert "Week so far" in reply
        assert "1. Alice" in reply

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


# ---------------------------------------------------------------------------
# brag / gripe Easter-egg broadcasts
# ---------------------------------------------------------------------------


class TestTauntCommands:
    """Both ``brag`` and ``gripe`` broadcast a competitive nudge to
    every recently-active player except the sender. Per-day per-command
    cooldown stops one bored player from spamming the group."""

    @staticmethod
    def _seed_active(repo: InMemoryRepository, *names_phones, today):
        """Seed each (whatsapp_id, display_name) pair with a single
        score yesterday so they count as active."""
        from app.puzzles import la_date
        from datetime import timedelta as _td
        ref = la_date(today) - _td(days=1)
        pno = 700
        for wid, name in names_phones:
            p = repo.get_or_create_player(wid, name)
            repo.insert_score(
                player_id=p.id, game="queens", puzzle_no=pno,
                puzzle_date=ref, raw_score=10, share_text="x",
            )
            pno += 1

    def test_unknown_kind_raises(self, repo):
        from app.jobs import run_taunt
        with pytest.raises(ValueError):
            run_taunt(
                repo, _settings_with_default_games(),
                kind="bogus", sender_id=1, sender_name="Alice",
                sender_whatsapp_id="whatsapp:+61400000001", now=NOW,
            )

    def test_brag_broadcasts_to_other_active_players(self, repo):
        from unittest.mock import patch
        self._seed_active(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            ("whatsapp:+61400000003", "Charlie"),
            today=NOW,
        )
        with patch("app.jobs.send_dm", return_value=True) as mock_dm:
            reply = handle_inbound(
                repo,
                from_="whatsapp:+61400000001",
                body="brag",
                profile_name="Alice",
                now=NOW,
                settings=_settings_with_default_games(),
            )
        # Bob and Charlie should each have received a DM; Alice should not.
        recipients = [c.args[1] for c in mock_dm.call_args_list]
        assert set(recipients) == {
            "whatsapp:+61400000002",
            "whatsapp:+61400000003",
        }
        assert "whatsapp:+61400000001" not in recipients
        # All recipients get the same body, named after the sender.
        bodies = {c.args[2] for c in mock_dm.call_args_list}
        assert len(bodies) == 1
        assert "Alice" in bodies.pop()
        # Confirmation reply names the count.
        assert reply is not None
        assert "Sent `brag` to 2 players" in reply

    def test_gripe_uses_a_different_pool_to_brag(self, repo):
        from unittest.mock import patch
        self._seed_active(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            today=NOW,
        )
        with patch("app.jobs.send_dm", return_value=True) as mock_dm:
            handle_inbound(
                repo, from_="whatsapp:+61400000001", body="gripe",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        gripe_body = mock_dm.call_args_list[0].args[2]

        # Reset cooldown for the same sender to compare bodies cleanly.
        repo._taunt_log.clear()
        with patch("app.jobs.send_dm", return_value=True) as mock_dm2:
            handle_inbound(
                repo, from_="whatsapp:+61400000001", body="brag",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        brag_body = mock_dm2.call_args_list[0].args[2]
        assert gripe_body != brag_body

    def test_cooldown_blocks_second_use_same_day(self, repo):
        from unittest.mock import patch
        self._seed_active(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            today=NOW,
        )
        with patch("app.jobs.send_dm", return_value=True):
            first = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="brag",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        assert first is not None and "Sent `brag`" in first

        # Second invocation same day must hit the cooldown.
        with patch("app.jobs.send_dm", return_value=True) as mock_dm:
            second = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="brag",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        assert second is not None
        assert "already used `brag`" in second
        mock_dm.assert_not_called()

    def test_brag_and_gripe_have_independent_cooldowns(self, repo):
        from unittest.mock import patch
        self._seed_active(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            today=NOW,
        )
        with patch("app.jobs.send_dm", return_value=True):
            r1 = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="brag",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
            r2 = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="gripe",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        assert r1 is not None and "Sent `brag`" in r1
        assert r2 is not None and "Sent `gripe`" in r2

    def test_no_audience_returns_lonely_reply_and_no_cooldown_burned(self, repo):
        from unittest.mock import patch
        # Only the sender exists in the active window — nobody else to taunt.
        self._seed_active(
            repo, ("whatsapp:+61400000001", "Alice"), today=NOW,
        )
        with patch("app.jobs.send_dm") as mock_dm:
            reply = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="brag",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        mock_dm.assert_not_called()
        assert reply is not None
        assert "active window" in reply
        # Cooldown must NOT have been recorded — the user shouldn't burn
        # their daily token on a no-op.
        from app.puzzles import la_date
        assert not repo.has_taunted_today(1, "brag", la_date(NOW))

    def test_flex_alias_works(self, repo):
        from unittest.mock import patch
        self._seed_active(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            today=NOW,
        )
        with patch("app.jobs.send_dm", return_value=True) as mock_dm:
            reply = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="flex",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        mock_dm.assert_called_once()
        assert reply is not None and "Sent `brag`" in reply

    def test_whinge_alias_works(self, repo):
        from unittest.mock import patch
        self._seed_active(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            today=NOW,
        )
        with patch("app.jobs.send_dm", return_value=True) as mock_dm:
            reply = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="whinge",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        mock_dm.assert_called_once()
        assert reply is not None and "Sent `gripe`" in reply

    def test_no_settings_returns_helpful_message(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="brag",
            profile_name="Alice", now=NOW, settings=None,
        )
        assert reply is not None
        assert "Twilio" in reply

    def test_brag_uses_random_selection_so_repeats_are_rare(self, repo):
        # 12 plain + N stat-aware brag templates → repeated brags on
        # the SAME day (cooldown reset between calls) should sample a
        # mix of templates rather than landing on the same one each
        # time. Threshold: at least 2 distinct bodies across 5 calls.
        from unittest.mock import patch
        self._seed_active(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            today=NOW,
        )
        bodies = set()
        with patch("app.jobs.send_dm", return_value=True) as mock_dm:
            for _ in range(5):
                repo._taunt_log.clear()  # bypass cooldown for the test
                handle_inbound(
                    repo, from_="whatsapp:+61400000001", body="brag",
                    profile_name="Alice", now=NOW,
                    settings=_settings_with_default_games(),
                )
                bodies.add(mock_dm.call_args.args[2])
        # 5 random picks from a 24+ template pool → very likely 2+
        # distinct bodies. (Probability of all 5 identical: ~1/24^4.)
        assert len(bodies) >= 2, (
            f"brag selection looks deterministic — only got: {bodies}"
        )

    def test_stat_aware_template_can_reference_today_score(self, repo):
        # Force a deterministic "stat-aware always wins" scenario by
        # checking against the rendered body when the sender has a
        # rich stat surface to draw from. We can't pin the exact
        # template (random.choice), but we CAN run several attempts
        # and assert at least one references the sender's actual
        # game/score data.
        from unittest.mock import patch
        from app.puzzles import la_date

        today_la = la_date(NOW)
        alice = repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        bob = repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        # Alice's actual day on Queens — sets the stat surface so
        # stat-aware brag templates can reference {best_score} and
        # {best_game}. Bob is just the audience.
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=today_la, raw_score=14, share_text="x",
        )
        repo.insert_score(
            player_id=bob.id, game="queens", puzzle_no=714,
            puzzle_date=today_la, raw_score=50, share_text="x",
        )
        seen_stat_aware = False
        with patch("app.jobs.send_dm", return_value=True) as mock_dm:
            for _ in range(20):
                repo._taunt_log.clear()
                handle_inbound(
                    repo, from_="whatsapp:+61400000001", body="brag",
                    profile_name="Alice", now=NOW,
                    settings=_settings_with_default_games(),
                )
                body = mock_dm.call_args.args[2]
                # Stat-aware bodies mention "0:14" (Alice's score),
                # "Queens", or her weekly rank "#1" / "#2".
                if "0:14" in body or "Queens" in body or "#1" in body:
                    seen_stat_aware = True
                    break
        assert seen_stat_aware, (
            "20 random brags fired without any stat-aware template — "
            "filtering may be excluding all stat-aware variants"
        )

    def test_two_senders_same_day_get_different_brags(self, repo):
        # The original day-ordinal selection bug: both senders on the
        # same day got byte-identical brags. With random + stat-
        # aware, two different senders should produce different
        # rendered text (different stats + random selection).
        from unittest.mock import patch
        from app.puzzles import la_date

        today_la = la_date(NOW)
        alice = repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        bob = repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        charlie = repo.get_or_create_player("whatsapp:+61400000003", "Charlie")
        # Different scores so stats differ.
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=today_la, raw_score=14, share_text="x",
        )
        repo.insert_score(
            player_id=bob.id, game="tango", puzzle_no=554,
            puzzle_date=today_la, raw_score=45, share_text="x",
        )
        # Charlie just the audience.
        repo.insert_score(
            player_id=charlie.id, game="zip", puzzle_no=414,
            puzzle_date=today_la, raw_score=20, share_text="x",
        )

        with patch("app.jobs.send_dm", return_value=True) as mock_dm:
            handle_inbound(
                repo, from_="whatsapp:+61400000001", body="brag",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
            alice_body = mock_dm.call_args.args[2]
            handle_inbound(
                repo, from_="whatsapp:+61400000002", body="brag",
                profile_name="Bob", now=NOW,
                settings=_settings_with_default_games(),
            )
            bob_body = mock_dm.call_args.args[2]

        # Each names the brag-er, so trivially different on the
        # name token. Stronger assertion: they're not byte-identical
        # after the name swap either (the old day-ordinal selection
        # would have produced the same template-text for both).
        assert alice_body != bob_body
        assert alice_body.replace("Alice", "X") != bob_body.replace("Bob", "X")


# ---------------------------------------------------------------------------
# nag / blast / poke — manual fan-out of the morning nudge
# ---------------------------------------------------------------------------


class TestNagCommand:
    """``nag`` (aliases ``blast``, ``poke``) is the on-demand version of
    the 08:30 Sydney morning nudge: DMs every active player who hasn't
    finished today's games, excluding the sender themself. 1/day per-
    sender cooldown."""

    @staticmethod
    def _seed_active_with_no_scores_today(repo, *names_phones, today):
        """Seed each (whatsapp_id, display_name) with a score yesterday
        (so they count as active) and nothing today (so they're behind
        on every enabled game)."""
        from app.puzzles import la_date
        from datetime import timedelta as _td
        ref = la_date(today) - _td(days=1)
        pno = 700
        for wid, name in names_phones:
            p = repo.get_or_create_player(wid, name)
            repo.insert_score(
                player_id=p.id, game="queens", puzzle_no=pno,
                puzzle_date=ref, raw_score=10, share_text="x",
            )
            pno += 1

    def test_nag_dms_active_players_who_havent_played_today(self, repo):
        from unittest.mock import patch
        self._seed_active_with_no_scores_today(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            ("whatsapp:+61400000003", "Charlie"),
            today=NOW,
        )
        with patch("app.jobs.send_dm", return_value=True) as mock_dm:
            reply = handle_inbound(
                repo,
                from_="whatsapp:+61400000001",
                body="nag",
                profile_name="Alice",
                now=NOW,
                settings=_settings_with_default_games(),
            )
        recipients = [c.args[1] for c in mock_dm.call_args_list]
        # Sender excluded; Bob and Charlie nudged.
        assert set(recipients) == {
            "whatsapp:+61400000002",
            "whatsapp:+61400000003",
        }
        assert "whatsapp:+61400000001" not in recipients
        assert reply is not None
        assert "Nudged 2 players" in reply
        assert "Bob" in reply and "Charlie" in reply

    def test_nag_skips_player_already_done_today(self, repo):
        from unittest.mock import patch
        from app.puzzles import la_date
        self._seed_active_with_no_scores_today(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            today=NOW,
        )
        # Bob has played every enabled game today — should NOT be nudged.
        bob = repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        today_la = la_date(NOW)
        pno = 800
        for game in {"queens", "tango", "zip", "patches", "mini_sudoku"}:
            repo.insert_score(
                player_id=bob.id, game=game, puzzle_no=pno,
                puzzle_date=today_la, raw_score=10, share_text="x",
            )
            pno += 1
        with patch("app.jobs.send_dm", return_value=True) as mock_dm:
            reply = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="nag",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        mock_dm.assert_not_called()
        assert reply is not None
        assert "nothing to nag about" in reply

    def test_nag_cooldown_blocks_second_use_same_day(self, repo):
        from unittest.mock import patch
        self._seed_active_with_no_scores_today(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            today=NOW,
        )
        with patch("app.jobs.send_dm", return_value=True):
            first = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="nag",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        assert first is not None and "Nudged 1 player" in first

        with patch("app.jobs.send_dm", return_value=True) as mock_dm:
            second = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="nag",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        assert second is not None
        assert "already nagged" in second
        mock_dm.assert_not_called()

    def test_nag_no_targets_keeps_cooldown_unburned(self, repo):
        from unittest.mock import patch
        from app.puzzles import la_date
        # Only the sender exists in the active window — nobody else
        # to nag. Cooldown must NOT be burned.
        self._seed_active_with_no_scores_today(
            repo, ("whatsapp:+61400000001", "Alice"), today=NOW,
        )
        with patch("app.jobs.send_dm") as mock_dm:
            reply = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="nag",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        mock_dm.assert_not_called()
        assert reply is not None and "nothing to nag about" in reply
        assert not repo.has_taunted_today(1, "nag", la_date(NOW))

    def test_blast_alias_works(self, repo):
        from unittest.mock import patch
        self._seed_active_with_no_scores_today(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            today=NOW,
        )
        with patch("app.jobs.send_dm", return_value=True) as mock_dm:
            reply = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="blast",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        mock_dm.assert_called_once()
        assert reply is not None and "Nudged 1 player" in reply

    def test_poke_alias_works(self, repo):
        from unittest.mock import patch
        self._seed_active_with_no_scores_today(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            today=NOW,
        )
        with patch("app.jobs.send_dm", return_value=True) as mock_dm:
            reply = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="poke",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        mock_dm.assert_called_once()
        assert reply is not None and "Nudged 1 player" in reply

    def test_nag_skips_opted_out_players(self, repo):
        from unittest.mock import patch
        self._seed_active_with_no_scores_today(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            today=NOW,
        )
        bob = repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        repo.set_notifications_enabled(bob.id, False)
        with patch("app.jobs.send_dm") as mock_dm:
            reply = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="nag",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        mock_dm.assert_not_called()
        assert reply is not None and "nothing to nag about" in reply

    def test_nag_no_settings_returns_helpful_message(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="nag",
            profile_name="Alice", now=NOW, settings=None,
        )
        assert reply is not None
        assert "Twilio" in reply
