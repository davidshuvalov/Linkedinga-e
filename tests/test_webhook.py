"""Tests for ``app.webhook.handle_inbound``.

Uses :class:`InMemoryRepository` rather than mocks so assertions can observe
the actual state the repo ended up in after handling a message.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.parsers import format_raw_score
from app.webhook import handle_inbound

from .conftest import TestRepo

SYDNEY = ZoneInfo("Australia/Sydney")
NOW = datetime(2026, 4, 14, 19, 0, tzinfo=SYDNEY)


@pytest.fixture
def repo() -> TestRepo:
    """Test repo seeded with a default group + auto-onboarding so
    existing webhook tests don't need to hit the onboarding gate
    explicitly. Group-isolation tests still spin up extra groups via
    :func:`make_extra_group`."""
    return TestRepo()


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

    def test_unrelated_chatter_gets_short_pointer(self, repo):
        # Bot operates in 1:1 DMs — silence left users guessing. We
        # reply with a one-liner pointing at ``help`` rather than
        # dumping the full command list every time someone says hi.
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="hey what's for dinner",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is not None
        assert "don't understand" in reply.lower()
        assert "help" in reply.lower()
        # Help blurb itself should NOT be inlined — that's the change.
        assert "stats" not in reply
        assert "recap" not in reply
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
        assert "don't understand" in reply.lower()
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
        for keyword in ("stats", "recap", "week", "ultrahelp"):
            assert keyword in reply
        # Format hint should appear too so users see how to submit.
        assert "LinkedIn" in reply

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


def _seed_sender_played_all_today(
    repo, sender_whatsapp_id, sender_name, today_la, enabled_games
):
    """Seed the sender with one score per enabled game on ``today_la``.
    Used by recap/leaderboard tests that want to reach the renderer
    past the no-peek gate, which requires the requester to have
    submitted every enabled game today before today's competitive
    data unlocks."""
    player = repo.get_or_create_player(sender_whatsapp_id, sender_name)
    pno = 900
    for game in sorted(enabled_games):
        repo.insert_score(
            player_id=player.id, game=game, puzzle_no=pno,
            puzzle_date=today_la, raw_score=99,
            share_text=f"{game} seed",
        )
        pno += 1
    return player


class TestLeaderboardCommand:
    def test_leaderboard_today_blocked_until_sender_plays_all_games(self, repo):
        # No-peek gate: today's leaderboard is locked until the
        # requester has submitted every enabled game today. Empty DB +
        # sender hasn't played → blocked.
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="leaderboard",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "No peeking" in reply
        assert "Still to play" in reply

    def test_leaderboard_lists_players_ranked(self, repo):
        from app.puzzles import la_date
        today_la = la_date(NOW)
        # Sender must clear the no-peek gate before today's
        # leaderboard renders.
        settings = _settings_with_default_games()
        _seed_sender_played_all_today(
            repo, "whatsapp:+61400000001", "Alice",
            today_la, settings.enabled_games,
        )
        _seed_three_queens(repo, today_la)
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="leaderboard",
            profile_name="Alice", now=NOW,
            settings=settings,
        )
        assert "1. Alice" in reply
        assert "2. Bob" in reply
        assert "3. Charlie" in reply

    def test_leaderboard_per_game_filter(self, repo):
        from app.puzzles import la_date
        today_la = la_date(NOW)
        settings = _settings_with_default_games()
        _seed_sender_played_all_today(
            repo, "whatsapp:+61400000001", "Alice",
            today_la, settings.enabled_games,
        )
        _seed_three_queens(repo, today_la)
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="leaderboard queens",
            profile_name="Alice", now=NOW,
            settings=settings,
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
        assert "don't understand" in reply.lower()

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
        assert "don't understand" in reply.lower() or "empty" in reply.lower()

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
        twilio_status_callback_url="",
        supabase_url="",
        supabase_key="",
        timezone_name="Australia/Sydney",
        enabled_games=frozenset(
            {"queens", "tango", "zip", "patches", "mini_sudoku"}
        ),
    )


class TestRecapCommand:
    def test_recap_today_blocked_when_sender_hasnt_played(self, repo):
        # No-peek gate: today's recap is locked behind the requester
        # having submitted at least one game today. Empty DB + sender
        # hasn't played → blocked entirely.
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="recap",
            profile_name="Alice",
            now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert "No peeking" in reply
        assert "Today's games:" in reply

    def test_recap_includes_week_so_far(self, repo):
        # Seed two scores on the LA day matching NOW.
        from app.puzzles import la_date

        today_la = la_date(NOW)
        settings = _settings_with_default_games()
        # Sender must clear the no-peek gate before today's recap
        # renders the full week-so-far block.
        _seed_sender_played_all_today(
            repo, "whatsapp:+61400000001", "Alice",
            today_la, settings.enabled_games,
        )
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
            settings=settings,
        )
        assert "Queens #714" in reply
        assert "Week so far:" in reply
        assert "1. Alice" in reply

    def test_today_is_alias_for_recap(self, repo):
        from app.puzzles import la_date
        settings = _settings_with_default_games()
        _seed_sender_played_all_today(
            repo, "whatsapp:+61400000001", "Alice",
            la_date(NOW), settings.enabled_games,
        )
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="today",
            profile_name="Alice",
            now=NOW,
            settings=settings,
        )
        assert "Daily recap" in reply

    def test_recap_case_insensitive(self, repo):
        from app.puzzles import la_date
        settings = _settings_with_default_games()
        _seed_sender_played_all_today(
            repo, "whatsapp:+61400000001", "Alice",
            la_date(NOW), settings.enabled_games,
        )
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="RECAP",
            profile_name="Alice",
            now=NOW,
            settings=settings,
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
        # Charlie is the requester (and has played all of today's
        # enabled games to clear the no-peek gate). Alice played
        # yesterday but skipped today → nag fires for Alice.
        from datetime import timedelta
        from app.puzzles import la_date

        today = la_date(NOW)
        yesterday = today - timedelta(days=1)
        settings = _settings_with_default_games()
        self._seed_day(repo, yesterday, 1, "Alice", 10, puzzle_no=713)
        self._seed_day(repo, today, 2, "Bob", 12, puzzle_no=714)
        _seed_sender_played_all_today(
            repo, "whatsapp:+61400000003", "Charlie",
            today, settings.enabled_games,
        )

        reply = handle_inbound(
            repo, from_="whatsapp:+61400000003",
            body="recap",
            profile_name="Charlie", now=NOW,
            settings=settings,
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
                group_id=repo.default_group.id,
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

    def test_all_sends_failing_does_not_burn_cooldown(self, repo):
        from unittest.mock import patch
        from app.puzzles import la_date

        # Bob is in the audience but Twilio rejects every send (e.g.
        # nobody's inside their 24h customer-care window). The user
        # should be told it failed AND keep their daily token so they
        # can try again later.
        self._seed_active(
            repo,
            ("whatsapp:+61400000001", "Alice"),
            ("whatsapp:+61400000002", "Bob"),
            today=NOW,
        )
        with patch("app.jobs.send_dm", return_value=False) as mock_dm:
            reply = handle_inbound(
                repo, from_="whatsapp:+61400000001", body="brag",
                profile_name="Alice", now=NOW,
                settings=_settings_with_default_games(),
            )
        # Tried to send (Bob was a target) but all attempts failed.
        mock_dm.assert_called()
        assert reply is not None
        assert "all sends failed" in reply
        assert "Cooldown not burned" in reply
        # Cooldown must NOT be burned — the user can retry later.
        alice = repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        assert not repo.has_taunted_today(alice.id, "brag", la_date(NOW))

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


# ---------------------------------------------------------------------------
# 42 — hidden founder-bio Easter egg
# ---------------------------------------------------------------------------


class TestEasterEgg42:
    """DMing ``42`` returns the creator's bio. Hidden — not in help."""

    def test_42_returns_founder_bio(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="42",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None
        assert "Founder Lore" in reply
        assert "David" in reply

    def test_42_works_with_surrounding_whitespace(self, repo):
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="  42  ",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert reply is not None and "Founder Lore" in reply

    def test_42_is_not_advertised_in_help_text(self, repo):
        # The trigger is intentionally hidden — finding it is the point.
        from app.webhook import _HELP_TEXT
        assert "42" not in _HELP_TEXT


# ---------------------------------------------------------------------------
# Score-confirmation reply consolidation
# ---------------------------------------------------------------------------


class TestConfirmationConsolidation:
    """The score-confirmation reply folds in the personal-best /
    worst-of-day trigger body and the day-complete summary so the
    submitter gets one consolidated WhatsApp message instead of
    two or three separate sends."""

    def test_pb_trigger_appended_to_confirmation(self, repo):
        from unittest.mock import patch
        settings = _settings_with_default_games()
        with patch("app.notifications.send_dm") as mock_dm:
            reply = handle_inbound(
                repo,
                from_="whatsapp:+61400000001",
                body="Queens #714\n0:42",
                profile_name="Alice", now=NOW,
                enabled_games=settings.enabled_games,
                settings=settings,
            )
        # Confirmation header is always present.
        assert reply is not None
        assert "Got it, Alice. Queens #714: 0:42." in reply
        # Trigger body appended (Alice opened today's Queens — at
        # least one of the first-today / best-of-day templates fires).
        assert reply.count("\n\n") >= 1
        # No separate Twilio DM was sent — the trigger body rides
        # inside the webhook's TwiML reply instead.
        assert mock_dm.call_count == 0

    def test_day_complete_summary_appended_to_final_submission(self, repo):
        from unittest.mock import patch
        settings = _settings_with_default_games()
        submissions = [
            ("Queens #714", "0:42"),
            ("Tango #614", "1:10"),
            ("Zip #414", "0:33"),
            ("Patches #214", "1:55"),
            ("Mini Sudoku #114", "0:50"),  # 5th and final
        ]
        last_reply = None
        with patch("app.notifications.send_dm") as mock_dm:
            for header, score in submissions:
                last_reply = handle_inbound(
                    repo,
                    from_="whatsapp:+61400000001",
                    body=f"{header}\n{score}",
                    profile_name="Alice", now=NOW,
                    enabled_games=settings.enabled_games,
                    settings=settings,
                )
        assert last_reply is not None
        # 5th submit's reply must include the day-complete scorecard.
        assert "Day done, Alice" in last_reply
        assert "Today's total" in last_reply
        # And it still has the score-confirmation header at the top.
        assert "Got it, Alice. Mini Sudoku #114: 0:50." in last_reply
        # No separate Twilio DM — both trigger and day-complete
        # bodies are consolidated into the TwiML reply.
        assert mock_dm.call_count == 0


# ---------------------------------------------------------------------------
# No-peek gate on today's recap / leaderboard
# ---------------------------------------------------------------------------


class TestNoPeekGate:
    """Today's recap and leaderboard are gated on the requester's own
    submissions, LinkedIn-style:

    - 0 games submitted → terse no-peek reply
    - some submitted → recap restricted to those games, no aggregates;
      leaderboard blocked entirely
    - all submitted → full views (the prior behavior)

    Past-day commands (yesterday, recap YYYY-MM-DD, leaderboard
    yesterday, etc.) are never gated."""

    def _seed_other_player_all_games_today(
        self, repo, ws_id, name, today_la, enabled
    ):
        """Seed a non-sender with all today's games so today's
        rankings have someone to render."""
        player = repo.get_or_create_player(ws_id, name)
        pno = 800
        for game in sorted(enabled):
            repo.insert_score(
                player_id=player.id, game=game, puzzle_no=pno,
                puzzle_date=today_la, raw_score=50,
                share_text=f"{game} other-seed",
            )
            pno += 1

    # --- recap: zero submissions ---------------------------------------

    def test_recap_today_blocks_when_sender_played_nothing(self, repo):
        from app.puzzles import la_date
        settings = _settings_with_default_games()
        # Other players' scores exist; sender has none.
        self._seed_other_player_all_games_today(
            repo, "whatsapp:+61400000002", "Bob",
            la_date(NOW), settings.enabled_games,
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="recap",
            profile_name="Alice", now=NOW, settings=settings,
        )
        assert "No peeking" in reply
        assert "Bob" not in reply  # zero competitive data leaks

    # --- recap: partial submissions ------------------------------------

    def test_recap_today_partial_shows_played_games_only(self, repo):
        from app.puzzles import la_date
        settings = _settings_with_default_games()
        today = la_date(NOW)
        # Bob has played all enabled games today (audience).
        self._seed_other_player_all_games_today(
            repo, "whatsapp:+61400000002", "Bob",
            today, settings.enabled_games,
        )
        # Alice has played queens + tango only — partial.
        alice = repo.get_or_create_player(
            "whatsapp:+61400000001", "Alice"
        )
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=today, raw_score=10, share_text="x",
        )
        repo.insert_score(
            player_id=alice.id, game="tango", puzzle_no=614,
            puzzle_date=today, raw_score=15, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="recap",
            profile_name="Alice", now=NOW, settings=settings,
        )
        # Played games render in the body. Check only the part above
        # the "Still to play" tail (which legitimately names the
        # locked games to nudge the user toward unlocking them).
        body_part = reply.split("Still to play:")[0]
        assert "Queens #714" in body_part
        assert "Tango" in body_part
        assert "Zip" not in body_part
        assert "Patches" not in body_part
        assert "Mini Sudoku" not in body_part
        # Aggregate blocks suppressed.
        assert "Week so far" not in body_part
        # Tail nudges the user toward unlocking the rest.
        assert "Still to play" in reply
        assert "Submit those to unlock" in reply

    # --- recap: all submissions ----------------------------------------

    def test_recap_today_full_when_sender_played_everything(self, repo):
        from app.puzzles import la_date
        settings = _settings_with_default_games()
        today = la_date(NOW)
        _seed_sender_played_all_today(
            repo, "whatsapp:+61400000001", "Alice",
            today, settings.enabled_games,
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="recap",
            profile_name="Alice", now=NOW, settings=settings,
        )
        # Full recap shape: header + per-game + Week so far block.
        assert "Daily recap" in reply
        assert "Week so far" in reply
        assert "No peeking" not in reply

    # --- recap: past day is never gated --------------------------------

    def test_yesterday_recap_not_gated(self, repo):
        from datetime import timedelta
        from app.puzzles import la_date

        today = la_date(NOW)
        yesterday = today - timedelta(days=1)
        bob = repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        repo.insert_score(
            player_id=bob.id, game="queens", puzzle_no=713,
            puzzle_date=yesterday, raw_score=10, share_text="x",
        )
        # Alice has played NOTHING — but yesterday is not gated.
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="yesterday",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "Daily recap" in reply
        assert "Bob" in reply
        assert "No peeking" not in reply

    # --- leaderboard: gated until ALL games played ---------------------

    def test_leaderboard_today_blocks_partial_submitter(self, repo):
        from app.puzzles import la_date
        settings = _settings_with_default_games()
        today = la_date(NOW)
        # Alice has only played queens — partial. Even per-game
        # leaderboards lock until all games done.
        alice = repo.get_or_create_player(
            "whatsapp:+61400000001", "Alice"
        )
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=today, raw_score=10, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="leaderboard",
            profile_name="Alice", now=NOW, settings=settings,
        )
        assert "No peeking" in reply
        assert "Played today: Queens" in reply
        assert "Still to play" in reply

    def test_leaderboard_per_game_today_blocks_partial(self, repo):
        from app.puzzles import la_date
        settings = _settings_with_default_games()
        today = la_date(NOW)
        # Alice has played queens but not the rest. Even
        # `leaderboard queens` is blocked — strict gate.
        alice = repo.get_or_create_player(
            "whatsapp:+61400000001", "Alice"
        )
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=today, raw_score=10, share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001",
            body="leaderboard queens",
            profile_name="Alice", now=NOW, settings=settings,
        )
        assert "No peeking" in reply

    def test_leaderboard_lists_absent_active_players(self, repo):
        from datetime import timedelta
        from app.puzzles import la_date

        settings = _settings_with_default_games()
        today = la_date(NOW)
        # Sender clears the no-peek gate.
        _seed_sender_played_all_today(
            repo, "whatsapp:+61400000001", "Alice",
            today, settings.enabled_games,
        )
        # Charlie is recently active (played 5 days ago, inside the
        # 7-day active window) but hasn't submitted this Mon–Sun →
        # should appear in the "Haven't played" footer of today's
        # leaderboard. NOW is Tue 14 Apr 2026 → this week is Apr 13–19;
        # 5 days ago = Apr 9, which is in last week and still in the
        # 7-day active window.
        last_week_day = today - timedelta(days=5)
        charlie = repo.get_or_create_player(
            "whatsapp:+61400000003", "Charlie"
        )
        repo.insert_score(
            player_id=charlie.id, game="queens", puzzle_no=706,
            puzzle_date=last_week_day, raw_score=10,
            share_text="x",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001", body="leaderboard",
            profile_name="Alice", now=NOW, settings=settings,
        )
        assert "Haven't played this week" in reply
        assert "Charlie" in reply.split("Haven't played this week")[1]

    def test_leaderboard_yesterday_not_gated(self, repo):
        from datetime import timedelta
        from app.puzzles import la_date

        today = la_date(NOW)
        yesterday = today - timedelta(days=1)
        bob = repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        repo.insert_score(
            player_id=bob.id, game="queens", puzzle_no=713,
            puzzle_date=yesterday, raw_score=10, share_text="x",
        )
        # Alice has played nothing — but yesterday's leaderboard is
        # not gated.
        reply = handle_inbound(
            repo, from_="whatsapp:+61400000001",
            body="leaderboard yesterday",
            profile_name="Alice", now=NOW,
            settings=_settings_with_default_games(),
        )
        assert "No peeking" not in reply
        assert "Bob" in reply


# ---------------------------------------------------------------------------
# End-to-end submit-then-recap regression
# ---------------------------------------------------------------------------


class TestRecapAfterSubmitFlow:
    """End-to-end regression for the bug "after you submit today's
    scores you cannot use the prompts recap or today. It outputs nothing".

    Earlier no-peek tests reach into the repo with seed helpers; these
    drive everything through ``handle_inbound`` so the score-insert
    path, the early-fire recap hook, and the recap-command path all
    run for real, mirroring production wiring (``enabled_games``
    parameter + ``expected_puzzle_no`` validator)."""

    def _puzzle_no(self, game):
        from app.puzzles import expected_puzzle_no
        return expected_puzzle_no(game, NOW)

    def _share_for(self, game):
        """Realistic LinkedIn share-text for the puzzle LinkedIn would
        currently be serving at NOW. Uses the raw multi-line shape the
        parsers verify against (verified 2026-04 in parsers.py)."""
        pno = self._puzzle_no(game)
        if game == "queens":
            return f"Queens #{pno}\n0:10 👑\nlnkd.in/queens."
        if game == "tango":
            return f"Tango #{pno}\n0:15 🌗\nlnkd.in/tango."
        if game == "zip":
            return f"Zip #{pno}\n0:20 🏁\nlnkd.in/zip."
        if game == "patches":
            return f"Patches #{pno} | 0:25 🧶\nWith no hints\nlnkd.in/patches."
        if game == "mini_sudoku":
            return (
                f"Mini Sudoku #{pno} | 0:30 ✏️\n"
                "The classic game, made mini.\n"
                "lnkd.in/minisudoku."
            )
        raise AssertionError(f"unknown game {game!r}")

    def _submit(self, repo, settings, game, *, sender, name):
        from app.puzzles import expected_puzzle_no
        return handle_inbound(
            repo,
            from_=sender,
            body=self._share_for(game),
            profile_name=name,
            now=NOW,
            enabled_games=settings.enabled_games,
            expected_puzzle_no=expected_puzzle_no,
            settings=settings,
        )

    def _ask(self, repo, settings, command, *, sender, name):
        from app.puzzles import expected_puzzle_no
        return handle_inbound(
            repo,
            from_=sender,
            body=command,
            profile_name=name,
            now=NOW,
            enabled_games=settings.enabled_games,
            expected_puzzle_no=expected_puzzle_no,
            settings=settings,
        )

    def test_recap_after_submitting_all_today_returns_full_recap(self, repo):
        # Submit every enabled game via handle_inbound, then confirm
        # both `recap` and `today` produce the full daily-recap shape.
        # The previous regression: this path returned an empty reply.
        settings = _settings_with_default_games()
        sender = "whatsapp:+61400000001"
        for game in sorted(settings.enabled_games):
            ack = self._submit(repo, settings, game, sender=sender, name="Alice")
            assert ack and "Got it" in ack, (game, ack)

        for command in ("recap", "today"):
            reply = self._ask(
                repo, settings, command, sender=sender, name="Alice"
            )
            assert reply, f"empty reply for `{command}` after full submit"
            assert reply.strip(), f"whitespace-only reply for `{command}`"
            assert "No peeking" not in reply
            assert "Daily recap" in reply
            # Per-game block must be there.
            assert "Queens" in reply
            assert "Tango" in reply

    def test_today_after_partial_submit_returns_partial_recap(self, repo):
        # Submit a partial slate, then confirm `recap` / `today` aren't
        # silent — they must return the partial-peek shape with the
        # "Still to play" tail.
        settings = _settings_with_default_games()
        sender = "whatsapp:+61400000001"
        for game in ("queens", "tango"):
            ack = self._submit(repo, settings, game, sender=sender, name="Alice")
            assert ack and "Got it" in ack

        for command in ("recap", "today"):
            reply = self._ask(
                repo, settings, command, sender=sender, name="Alice"
            )
            assert reply, f"empty reply for `{command}` after partial submit"
            assert reply.strip()
            assert "Daily recap" in reply
            assert "Queens" in reply
            assert "Tango" in reply
            # Aggregate blocks suppressed; tail nudges the user toward
            # unlocking the rest.
            body_part = reply.split("Still to play:")[0]
            assert "Week so far" not in body_part
            assert "Still to play" in reply
            assert "Submit those to unlock" in reply

    def test_today_before_any_submit_returns_no_peek(self, repo):
        # Sanity check: with zero submissions today, the gate triggers
        # the no-peek reply (non-empty by design).
        settings = _settings_with_default_games()
        sender = "whatsapp:+61400000001"
        for command in ("recap", "today"):
            reply = self._ask(
                repo, settings, command, sender=sender, name="Alice"
            )
            assert reply, f"empty reply for `{command}` pre-submit"
            assert "No peeking" in reply
            assert "Today's games:" in reply


# ---------------------------------------------------------------------------
# Group commands + onboarding gate
# ---------------------------------------------------------------------------


class TestGroupOnboarding:
    """`group <name>` is the create-or-join verb every brand-new player
    must run before anything else. A player whose ``group_id`` is
    ``None`` (as in the raw :class:`InMemoryRepository`) is treated
    as un-onboarded; until they join a group, the dispatcher refuses
    every other command and every score submission."""

    def _raw_repo(self):
        # Bypass the conftest auto-onboarding so we can exercise the
        # un-onboarded state explicitly.
        from app.db import InMemoryRepository
        return InMemoryRepository()

    def test_brand_new_player_gets_onboarding_prompt(self):
        repo = self._raw_repo()
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000999",
            body="recap",
            profile_name="Newbie",
            now=NOW,
        )
        assert reply is not None
        # Full welcome intro — covers groups, posting scores, and
        # the notification rhythm so first-timers can self-onboard.
        assert "Welcome!" in reply
        assert "group <name>" in reply
        assert "GROUPS" in reply
        assert "POSTING SCORES" in reply
        assert "Queens #714" in reply  # the example
        assert "WHAT TO EXPECT" in reply
        assert "Daily recap" in reply
        assert "weekly wrap" in reply.lower()
        # Comfortably under WhatsApp's 1600-char ceiling.
        assert len(reply) <= 1500

    def test_help_pre_onboarding_shows_short_help(self):
        repo = self._raw_repo()
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000999",
            body="help",
            profile_name="Newbie",
            now=NOW,
        )
        assert reply is not None
        assert "haven't joined a group" in reply
        assert "group <name>" in reply

    def test_42_easter_egg_works_pre_onboarding(self):
        repo = self._raw_repo()
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000999",
            body="42",
            profile_name="Newbie",
            now=NOW,
        )
        assert reply is not None
        assert "Easter Egg" in reply

    def test_score_submission_blocked_pre_onboarding(self):
        repo = self._raw_repo()
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000999",
            body="Queens #714 | 0:30",
            profile_name="Newbie",
            now=NOW,
        )
        assert reply is not None
        # Same welcome intro as any other pre-onboarding command.
        assert "Welcome!" in reply
        assert "group <name>" in reply
        # Critically, no score row was created.
        assert len(repo.scores) == 0

    def test_group_creates_new_group(self):
        repo = self._raw_repo()
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000999",
            body="group Crew",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is not None
        assert "Created group" in reply
        assert "Crew" in reply
        # Player is onboarded.
        player = repo._players["whatsapp:+61400000999"]
        assert player.group_id is not None
        # Group exists with original casing preserved.
        group = repo.find_group_by_name("crew")
        assert group is not None
        assert group.name == "Crew"

    def test_group_joins_existing_case_insensitive(self):
        repo = self._raw_repo()
        repo.get_or_create_group("Crew")
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000999",
            body="GROUP crew",
            profile_name="Bob",
            now=NOW,
        )
        assert reply is not None
        assert "Joined group" in reply
        assert "Crew" in reply  # original casing in reply
        player = repo._players["whatsapp:+61400000999"]
        assert player.group_id is not None
        # Lookup confirms exactly one group was created.
        assert len(repo.list_groups()) == 1

    def test_group_empty_name_rejected(self):
        repo = self._raw_repo()
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000999",
            body="group ",
            profile_name="Alice",
            now=NOW,
        )
        # Trailing whitespace doesn't match _GROUP_RE → falls through
        # to the onboarding prompt, which is the right behaviour.
        assert reply is not None
        assert "Welcome!" in reply or "can't be empty" in reply

    def test_group_too_long_rejected(self):
        repo = self._raw_repo()
        long_name = "x" * 60
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000999",
            body=f"group {long_name}",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is not None
        assert "too long" in reply

    def test_score_submission_attaches_to_current_group(self):
        repo = self._raw_repo()
        # Onboard.
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="group Crew",
            profile_name="Alice",
            now=NOW,
        )
        # Submit.
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #365 | 1:23",
            profile_name="Alice",
            now=NOW,
        )
        crew = repo.find_group_by_name("Crew")
        assert crew is not None
        assert len(repo.scores) == 1
        assert repo.scores[0]["group_id"] == crew.id


class TestSwitchCommand:
    """`switch <name>` requires an existing group — the safer sibling
    of `group <name>` for users who don't want a typo to silently
    create a new group."""

    def _raw_repo(self):
        from app.db import InMemoryRepository
        return InMemoryRepository()

    def test_switch_to_missing_group_errors_with_hint(self):
        repo = self._raw_repo()
        # Pre-onboard so we hit the switch path rather than the
        # onboarding gate.
        repo.get_or_create_group("Crew")
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="group Crew",
            profile_name="Alice",
            now=NOW,
        )
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="switch Nonexistent",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is not None
        assert "No group called" in reply
        assert "group Nonexistent" in reply  # hint to use create-verb

    def test_switch_to_existing_group_moves_player(self):
        repo = self._raw_repo()
        repo.get_or_create_group("OriginalCrew")
        repo.get_or_create_group("NewCrew")
        # Onboard into OriginalCrew + submit a score there.
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="group OriginalCrew",
            profile_name="Alice",
            now=NOW,
        )
        handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="Queens #365 | 1:23",
            profile_name="Alice",
            now=NOW,
        )
        original = repo.find_group_by_name("OriginalCrew")
        assert original is not None
        assert repo.scores[0]["group_id"] == original.id
        # Switch.
        reply = handle_inbound(
            repo,
            from_="whatsapp:+61400000001",
            body="switch NewCrew",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is not None
        assert "Switched to" in reply
        assert "NewCrew" in reply
        assert "OriginalCrew" in reply  # mentions the old group
        # Old score stays in OriginalCrew.
        assert repo.scores[0]["group_id"] == original.id


class TestCrossGroupIsolation:
    """Players in different groups don't see each other's scores in
    any of the read commands (recap, leaderboard, stats, pb, vs)."""

    def _two_group_repo(self):
        """Set up: two groups A and B, one player each, one queens
        score per player. Different puzzle numbers so the dedup
        constraint doesn't intervene."""
        from app.db import InMemoryRepository
        repo = InMemoryRepository()
        a = repo.get_or_create_group("ACrew")
        b = repo.get_or_create_group("BCrew")
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        bob = repo.get_or_create_player("whatsapp:+2", "Bob")
        repo.set_player_group(alice.id, a.id)
        repo.set_player_group(bob.id, b.id)
        repo.insert_score(
            player_id=alice.id, group_id=a.id, game="queens",
            puzzle_no=714, puzzle_date=NOW.date(), raw_score=10,
            share_text="Queens #714 (Alice)",
        )
        repo.insert_score(
            player_id=bob.id, group_id=b.id, game="queens",
            puzzle_no=714, puzzle_date=NOW.date(), raw_score=20,
            share_text="Queens #714 (Bob)",
        )
        return repo, a, b

    def test_stats_only_shows_own_group(self):
        repo, _, _ = self._two_group_repo()
        reply = handle_inbound(
            repo,
            from_="whatsapp:+1",
            body="stats",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is not None
        assert "Alice" in reply
        # 1 submission total (alice's own; bob's is in the other group).
        assert "Submissions: 1" in reply

    def test_pb_only_shows_own_group_history(self):
        repo, _, _ = self._two_group_repo()
        reply = handle_inbound(
            repo,
            from_="whatsapp:+1",
            body="pb",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is not None
        assert "Alice" in reply
        assert "Queens" in reply

    def test_vs_only_finds_opponents_in_same_group(self):
        repo, _, _ = self._two_group_repo()
        # Alice tries to compare with Bob — Bob's in the other group,
        # so vs should report no shared rounds (or "not found").
        reply = handle_inbound(
            repo,
            from_="whatsapp:+1",
            body="vs Bob",
            profile_name="Alice",
            now=NOW,
        )
        assert reply is not None
        # Either "couldn't find" (Bob isn't visible) or "no shared
        # rounds" — both indicate isolation.
        assert "Couldn't find" in reply or "No shared rounds" in reply

    def test_recap_only_aggregates_own_group(self):
        repo, _, _ = self._two_group_repo()
        # Configure alice as a sender who's "played all" in her group
        # so the recap unlocks. Her group has only one enabled game —
        # queens — once we restrict settings.
        from app.config import Settings
        settings = Settings(
            twilio_account_sid="", twilio_auth_token="",
            twilio_whatsapp_from="", twilio_recap_to="",
            twilio_status_callback_url="",
            supabase_url="", supabase_key="",
            timezone_name="Australia/Sydney",
            enabled_games=frozenset({"queens"}),
        )
        reply = handle_inbound(
            repo,
            from_="whatsapp:+1",
            body="recap",
            profile_name="Alice",
            now=NOW,
            settings=settings,
        )
        assert reply is not None
        # Alice's group — only her queens row should appear; Bob is
        # in the other group.
        assert "Alice" in reply
        assert "Bob" not in reply


# ---------------------------------------------------------------------------
# Personal analytics commands: history, trends, best/worst day, pace
# ---------------------------------------------------------------------------


class TestHistoryCommand:
    """``history`` and ``history <game>`` show personal score history."""

    def _setup_alice_with_zip_scores(self, repo):
        from datetime import date, timedelta

        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        base = date(2026, 4, 1)
        # 5 zip scores, increasing (first is the PB at 30s)
        for i, raw in enumerate([30, 45, 50, 60, 80]):
            repo.insert_score(
                player_id=alice.id, game="zip", puzzle_no=420 + i,
                puzzle_date=base + timedelta(days=i), raw_score=raw,
                share_text=f"Zip #{420 + i}",
            )
        return alice

    def test_history_unknown_game_returns_error(self, repo):
        repo.get_or_create_player("whatsapp:+1", "Alice")
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="history widgets",
            profile_name="Alice", now=NOW,
        )
        assert "Unknown game" in reply

    def test_history_zip_shows_scores_newest_first(self, repo):
        self._setup_alice_with_zip_scores(repo)
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="history zip",
            profile_name="Alice", now=NOW,
        )
        assert "Zip history for Alice" in reply
        # Most recent puzzle (424) should appear before oldest (420)
        assert reply.index("#424") < reply.index("#420")

    def test_history_marks_pb(self, repo):
        self._setup_alice_with_zip_scores(repo)
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="history zip",
            profile_name="Alice", now=NOW,
        )
        # PB is raw=30 = "0:30"; the ✓ PB marker should appear
        assert "✓ PB" in reply
        # Only one PB marker expected (the best score)
        assert reply.count("✓ PB") == 1

    def test_history_no_scores_returns_prompt(self, repo):
        repo.get_or_create_player("whatsapp:+1", "Alice")
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="history zip",
            profile_name="Alice", now=NOW,
        )
        assert "No Zip scores" in reply

    def test_history_bare_shows_all_games(self, repo):
        from datetime import date

        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        today = date(2026, 4, 14)
        repo.insert_score(
            player_id=alice.id, game="zip", puzzle_no=420,
            puzzle_date=today, raw_score=45, share_text="Zip",
        )
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=today, raw_score=30, share_text="Queens",
        )
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="history",
            profile_name="Alice", now=NOW,
        )
        assert "Zip" in reply
        assert "Queens" in reply


class TestTrendsCommand:
    """``trends`` shows per-game improvement/decline vs prior 4 weeks."""

    def _setup_trends(self, repo):
        from datetime import date, timedelta

        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        # NOW = 2026-04-14 LA. Current week starts Mon 2026-04-13.
        # last4: 2026-03-16 to 2026-04-12 (4 weeks prior to current week)
        # prior4: 2026-02-16 to 2026-03-15 (4 weeks before last4)
        last4_base = date(2026, 3, 16)   # in last4 range (Mon 16 Mar)
        prior4_base = date(2026, 2, 23)  # in prior4 range (Mon 23 Feb)

        for i in range(3):
            repo.insert_score(
                player_id=alice.id, game="zip", puzzle_no=400 + i,
                puzzle_date=last4_base + timedelta(days=i), raw_score=30,
                share_text="",
            )
        for i in range(3):
            repo.insert_score(
                player_id=alice.id, game="zip", puzzle_no=380 + i,
                puzzle_date=prior4_base + timedelta(days=i), raw_score=50,
                share_text="",
            )
        return alice

    def test_trends_shows_improvement(self, repo):
        self._setup_trends(repo)
        settings = _settings_with_default_games()
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="trends",
            profile_name="Alice", now=NOW, settings=settings,
        )
        assert "Trends for Alice" in reply
        # Zip went from avg 50 → avg 30 (faster): arrow should be ↓
        assert "↓" in reply

    def test_trends_insufficient_data_skips_game(self, repo):
        # Only 1 score per period (need ≥3) → skipped with "Not enough data"
        from datetime import date

        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        # Each date falls inside its respective window but only 1 score each.
        last4_date = date(2026, 3, 16)   # in last4 range
        prior4_date = date(2026, 2, 23)  # in prior4 range
        repo.insert_score(
            player_id=alice.id, game="zip", puzzle_no=400,
            puzzle_date=last4_date, raw_score=30, share_text="",
        )
        repo.insert_score(
            player_id=alice.id, game="zip", puzzle_no=380,
            puzzle_date=prior4_date, raw_score=50, share_text="",
        )
        settings = _settings_with_default_games()
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="trends",
            profile_name="Alice", now=NOW, settings=settings,
        )
        # Zip should appear in "Not enough data" list
        assert "Zip" in reply

    def test_trends_no_scores_returns_prompt(self, repo):
        repo.get_or_create_player("whatsapp:+1", "Alice")
        settings = _settings_with_default_games()
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="trends",
            profile_name="Alice", now=NOW, settings=settings,
        )
        assert "No scores" in reply


class TestBestWorstDayCommand:
    """``best day`` / ``worst day`` identify the player's top/bottom day."""

    def _setup_three_days(self, repo):
        from datetime import date

        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        # Day A: both games = PB (raw 10 each)
        # Day B: both games = worst (raw 90 each)
        # Day C: mixed (raw 30 and 60)
        day_a = date(2026, 4, 6)  # Monday
        day_b = date(2026, 4, 7)  # Tuesday
        day_c = date(2026, 4, 8)  # Wednesday

        for day, (q, z) in [(day_a, (10, 10)), (day_b, (90, 90)), (day_c, (30, 60))]:
            repo.insert_score(
                player_id=alice.id, game="queens", puzzle_no=700 + (day - day_a).days,
                puzzle_date=day, raw_score=q, share_text="",
            )
            repo.insert_score(
                player_id=alice.id, game="zip", puzzle_no=400 + (day - day_a).days,
                puzzle_date=day, raw_score=z, share_text="",
            )
        return alice, day_a, day_b

    def test_best_day_picks_highest_percentile_day(self, repo):
        _, day_a, _ = self._setup_three_days(repo)
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="best day",
            profile_name="Alice", now=NOW,
        )
        assert "Best day for Alice" in reply
        assert day_a.strftime("%d %b %Y") in reply

    def test_worst_day_picks_lowest_percentile_day(self, repo):
        _, _, day_b = self._setup_three_days(repo)
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="worst day",
            profile_name="Alice", now=NOW,
        )
        assert "Worst day for Alice" in reply
        assert day_b.strftime("%d %b %Y") in reply

    def test_best_day_marks_pb(self, repo):
        self._setup_three_days(repo)
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="best day",
            profile_name="Alice", now=NOW,
        )
        # Best day has PB scores for both games
        assert "✓ PB" in reply

    def test_best_day_no_scores_returns_prompt(self, repo):
        repo.get_or_create_player("whatsapp:+1", "Alice")
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="best day",
            profile_name="Alice", now=NOW,
        )
        assert "No scores" in reply


class TestPaceCommand:
    """``pace`` projects the player's weekly total through Sunday."""

    def _setup_pace(self, repo):
        from datetime import date

        # NOW is 2026-04-14 (Tuesday LA). Week starts Mon 2026-04-13.
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        monday = date(2026, 4, 13)
        # 2 queens scores in the week
        for i, raw in enumerate([20, 30]):
            repo.insert_score(
                player_id=alice.id, game="queens", puzzle_no=714 + i,
                puzzle_date=monday + __import__("datetime").timedelta(days=i),
                raw_score=raw, share_text="",
            )
        return alice

    def test_pace_shows_current_rank_and_projection(self, repo):
        self._setup_pace(repo)
        settings = _settings_with_default_games()
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="pace",
            profile_name="Alice", now=NOW, settings=settings,
        )
        assert "Pace for Alice" in reply
        assert "Current:" in reply
        assert "Projected:" in reply

    def test_pace_projects_from_current_week(self, repo):
        self._setup_pace(repo)
        settings = _settings_with_default_games()
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="pace",
            profile_name="Alice", now=NOW, settings=settings,
        )
        # Should show week number (week 16 in 2026-04-14 ISO calendar)
        assert "week" in reply.lower()

    def test_pace_no_scores_returns_prompt(self, repo):
        repo.get_or_create_player("whatsapp:+1", "Alice")
        settings = _settings_with_default_games()
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="pace",
            profile_name="Alice", now=NOW, settings=settings,
        )
        assert "No scores" in reply or "hasn't submitted" in reply


class TestByDayCommand:
    """``by day`` shows per-weekday avg/best/worst breakdown with quartile labels."""

    def _setup_multi_dow(self, repo):
        """Seed Alice with zip scores on several distinct weekdays.
        2026-04-06 Mon, 2026-04-07 Tue, 2026-04-08 Wed
        Three scores each day for a meaningful quartile + avg."""
        from datetime import date

        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        # Day plan: Mon=fast (20, 25, 22), Tue=medium (45, 50, 48), Wed=slow (80, 90, 85)
        entries = [
            (date(2026, 4, 6),  "zip", [20, 25, 22]),
            (date(2026, 4, 7),  "zip", [45, 50, 48]),
            (date(2026, 4, 8),  "zip", [80, 90, 85]),
        ]
        pno = 420
        for day, game, raws in entries:
            for raw in raws:
                repo.insert_score(
                    player_id=alice.id, game=game, puzzle_no=pno,
                    puzzle_date=day, raw_score=raw, share_text="",
                )
                pno += 1
        return alice

    def test_by_day_shows_weekday_rows(self, repo):
        self._setup_multi_dow(repo)
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="by day",
            profile_name="Alice", now=NOW,
        )
        assert "Day breakdown for Alice" in reply
        assert "Mon" in reply
        assert "Tue" in reply
        assert "Wed" in reply

    def test_by_day_shows_avg_best_worst(self, repo):
        self._setup_multi_dow(repo)
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="by day",
            profile_name="Alice", now=NOW,
        )
        # Monday best should be 0:20 (raw=20)
        assert "avg" in reply
        assert "best" in reply
        assert "worst" in reply

    def test_by_day_top_quarter_marker_on_best_dow(self, repo):
        self._setup_multi_dow(repo)
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="by day",
            profile_name="Alice", now=NOW,
        )
        # Mon average is ~22s which is in the top quarter of all 9 scores
        assert "top quarter" in reply

    def test_by_day_bottom_quarter_marker_on_worst_dow(self, repo):
        self._setup_multi_dow(repo)
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="by day",
            profile_name="Alice", now=NOW,
        )
        # Wed average is ~85s which is in the bottom quarter
        assert "bottom quarter" in reply

    def test_by_day_specific_game_filter(self, repo):
        self._setup_multi_dow(repo)
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="by day zip",
            profile_name="Alice", now=NOW,
        )
        assert "Zip" in reply
        # Shouldn't show other game headers like "Queens"
        assert "Queens" not in reply

    def test_by_day_no_scores_returns_prompt(self, repo):
        repo.get_or_create_player("whatsapp:+1", "Alice")
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="by day",
            profile_name="Alice", now=NOW,
        )
        assert "No scores" in reply or "Not enough" in reply

    def test_by_day_with_limit_shows_label(self, repo):
        self._setup_multi_dow(repo)
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="by day 5",
            profile_name="Alice", now=NOW,
        )
        assert "last 5" in reply

    def test_by_day_limit_restricts_to_recent_scores(self, repo):
        """With limit=1, only the 1 most-recent score per game is kept,
        so no weekday can have ≥ 2 plays → no weekday rows rendered."""
        self._setup_multi_dow(repo)
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="by day zip 1",
            profile_name="Alice", now=NOW,
        )
        # Only 1 score in window → no weekday has ≥2 plays → "Not enough data"
        assert "Not enough" in reply or "1 of" in reply

    def test_by_day_game_and_limit_any_order(self, repo):
        """'by day 5 zip' and 'by day zip 5' should produce the same output."""
        self._setup_multi_dow(repo)
        r1 = handle_inbound(
            repo, from_="whatsapp:+1", body="by day 5 zip",
            profile_name="Alice", now=NOW,
        )
        r2 = handle_inbound(
            repo, from_="whatsapp:+1", body="by day zip 5",
            profile_name="Alice", now=NOW,
        )
        assert r1 == r2


class TestTrackCommand:
    """``track`` sets per-group game tracking."""

    def test_track_sets_group_games(self, repo):
        repo.get_or_create_player("whatsapp:+1", "Alice")
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="track queens zip",
            profile_name="Alice", now=NOW,
        )
        assert "Queens" in reply
        assert "Zip" in reply
        # The group's enabled_games should now be set
        from app.db import InMemoryRepository
        group = repo.get_group(repo.default_group.id)
        assert group is not None
        assert group.enabled_games == frozenset({"queens", "zip"})

    def test_track_reset_clears_override(self, repo):
        repo.get_or_create_player("whatsapp:+1", "Alice")
        # First set games
        handle_inbound(
            repo, from_="whatsapp:+1", body="track queens",
            profile_name="Alice", now=NOW,
        )
        # Then reset
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="track reset",
            profile_name="Alice", now=NOW,
        )
        assert "reset" in reply.lower() or "global" in reply.lower()
        group = repo.get_group(repo.default_group.id)
        assert group is not None
        assert group.enabled_games is None

    def test_track_unknown_game_returns_error(self, repo):
        repo.get_or_create_player("whatsapp:+1", "Alice")
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="track widgets",
            profile_name="Alice", now=NOW,
        )
        assert "Unknown" in reply

    def test_track_all_enables_every_game(self, repo):
        from app.parsers import GAMES
        repo.get_or_create_player("whatsapp:+1", "Alice")
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="track all",
            profile_name="Alice", now=NOW,
        )
        assert "all games" in reply.lower() or "tracking" in reply.lower()
        group = repo.get_group(repo.default_group.id)
        assert group is not None
        assert group.enabled_games == frozenset(GAMES)

    def test_group_games_override_used_in_trends(self, repo):
        """After track sets group games, only those games appear in trends."""
        from datetime import date, timedelta

        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        # Seed enough zip scores in the two periods
        last4_base = date(2026, 3, 16)
        prior4_base = date(2026, 2, 23)
        for i in range(3):
            repo.insert_score(
                player_id=alice.id, game="zip", puzzle_no=400 + i,
                puzzle_date=last4_base + timedelta(days=i), raw_score=30, share_text="",
            )
            repo.insert_score(
                player_id=alice.id, game="zip", puzzle_no=380 + i,
                puzzle_date=prior4_base + timedelta(days=i), raw_score=50, share_text="",
            )
        # Also seed queens scores so they'd show up if queens were enabled
        for i in range(3):
            repo.insert_score(
                player_id=alice.id, game="queens", puzzle_no=700 + i,
                puzzle_date=last4_base + timedelta(days=i), raw_score=30, share_text="",
            )
            repo.insert_score(
                player_id=alice.id, game="queens", puzzle_no=680 + i,
                puzzle_date=prior4_base + timedelta(days=i), raw_score=50, share_text="",
            )
        # Set group to track only zip
        handle_inbound(
            repo, from_="whatsapp:+1", body="track zip",
            profile_name="Alice", now=NOW,
        )
        settings = _settings_with_default_games()
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="trends",
            profile_name="Alice", now=NOW, settings=settings,
        )
        # Zip should show trend; Queens should NOT appear (not tracked)
        assert "Zip" in reply
        assert "Queens" not in reply


# ---------------------------------------------------------------------------
# Global (cross-group) commands: recap, wrap, times
# ---------------------------------------------------------------------------


class TestGlobalRecapWrapTimes:
    """``global recap``, ``global wrap``, ``global times`` aggregate
    scores from every group and use the intersection of each group's
    enabled games."""

    def _two_group_repo_with_scores(self):
        """Two groups, one player each, both play queens on the same
        puzzle date. NOW is 2026-04-14 (Tue LA). We put yesterday's
        scores (Mon 2026-04-13) so 'global recap' (yesterday) returns them."""
        from datetime import date
        from app.db import InMemoryRepository

        repo = InMemoryRepository()
        ga = repo.get_or_create_group("Alpha")
        gb = repo.get_or_create_group("Beta")
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        bob = repo.get_or_create_player("whatsapp:+2", "Bob")
        repo.set_player_group(alice.id, ga.id)
        repo.set_player_group(bob.id, gb.id)

        yesterday = date(2026, 4, 13)  # Monday (same week as NOW)
        repo.insert_score(
            player_id=alice.id, group_id=ga.id, game="queens",
            puzzle_no=714, puzzle_date=yesterday, raw_score=30, share_text="",
        )
        repo.insert_score(
            player_id=bob.id, group_id=gb.id, game="queens",
            puzzle_no=714, puzzle_date=yesterday, raw_score=40, share_text="",
        )
        return repo, ga, gb, alice, bob

    def test_global_recap_includes_both_groups(self):
        repo, _, _, _, _ = self._two_group_repo_with_scores()
        settings = _settings_with_default_games()
        # Alice is in group Alpha — handle_inbound would normally scope to Alpha.
        # We call _handle_global_recap directly to bypass group routing.
        from app.webhook import _handle_global_recap
        reply = _handle_global_recap(
            repo, settings, NOW, group_id=1,
        )
        assert "Alice" in reply
        assert "Bob" in reply

    def test_global_recap_via_command(self):
        """'global recap' command returns cross-group data."""
        from datetime import date
        from app.db import InMemoryRepository

        repo = InMemoryRepository()
        ga = repo.get_or_create_group("Alpha")
        gb = repo.get_or_create_group("Beta")
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        bob = repo.get_or_create_player("whatsapp:+2", "Bob")
        repo.set_player_group(alice.id, ga.id)
        repo.set_player_group(bob.id, gb.id)

        yesterday = date(2026, 4, 13)
        repo.insert_score(
            player_id=alice.id, group_id=ga.id, game="queens",
            puzzle_no=714, puzzle_date=yesterday, raw_score=30, share_text="",
        )
        repo.insert_score(
            player_id=bob.id, group_id=gb.id, game="queens",
            puzzle_no=714, puzzle_date=yesterday, raw_score=40, share_text="",
        )
        # Alice issues "global recap"
        settings = _settings_with_default_games()
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="global recap",
            profile_name="Alice", now=NOW, settings=settings,
        )
        assert "Alice" in reply
        assert "Bob" in reply

    def test_global_wrap_includes_both_groups(self):
        from app.webhook import _handle_global_wrap
        repo, _, _, _, _ = self._two_group_repo_with_scores()
        settings = _settings_with_default_games()
        reply = _handle_global_wrap(repo, settings, NOW, group_id=1)
        assert "Alice" in reply
        assert "Bob" in reply

    def test_global_times_includes_both_groups(self):
        from app.webhook import _handle_global_times
        repo, _, _, _, _ = self._two_group_repo_with_scores()
        settings = _settings_with_default_games()
        reply = _handle_global_times(repo, settings, NOW, group_id=1)
        # Queens is a time game; both players appear
        assert "Alice" in reply or "Bob" in reply

    def test_global_uses_common_games_intersection(self):
        """When groups track different games, global uses the intersection."""
        from datetime import date
        from app.db import InMemoryRepository
        from app.webhook import _handle_global_recap

        repo = InMemoryRepository()
        ga = repo.get_or_create_group("Alpha")
        gb = repo.get_or_create_group("Beta")
        # Alpha tracks only queens; Beta tracks only zip
        repo.set_group_games(ga.id, frozenset({"queens"}))
        repo.set_group_games(gb.id, frozenset({"zip"}))
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        bob = repo.get_or_create_player("whatsapp:+2", "Bob")
        repo.set_player_group(alice.id, ga.id)
        repo.set_player_group(bob.id, gb.id)

        yesterday = date(2026, 4, 13)
        # Alice plays queens (tracked by Alpha), Bob plays zip (tracked by Beta)
        repo.insert_score(
            player_id=alice.id, group_id=ga.id, game="queens",
            puzzle_no=714, puzzle_date=yesterday, raw_score=30, share_text="",
        )
        repo.insert_score(
            player_id=bob.id, group_id=gb.id, game="zip",
            puzzle_no=420, puzzle_date=yesterday, raw_score=30, share_text="",
        )
        settings = _settings_with_default_games()
        # Intersection of {queens} ∩ {zip} = {} → no scores in common
        reply = _handle_global_recap(repo, settings, NOW, group_id=1)
        assert "No global scores" in reply

    def test_global_yesterday_alias(self):
        """'global yesterday' is an alias for global recap."""
        from datetime import date
        from app.db import InMemoryRepository

        repo = InMemoryRepository()
        ga = repo.get_or_create_group("Alpha")
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.set_player_group(alice.id, ga.id)
        yesterday = date(2026, 4, 13)
        repo.insert_score(
            player_id=alice.id, group_id=ga.id, game="queens",
            puzzle_no=714, puzzle_date=yesterday, raw_score=30, share_text="",
        )
        settings = _settings_with_default_games()
        reply = handle_inbound(
            repo, from_="whatsapp:+1", body="global yesterday",
            profile_name="Alice", now=NOW, settings=settings,
        )
        assert "Alice" in reply
