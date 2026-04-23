"""Unit tests for ``app.notifications`` — PB / worst-ever DM logic
and the "day complete" personal summary.

Focuses on the pure-function cores (``_classify``,
``render_personal_best_message``, ``render_day_complete_summary``,
``maybe_notify_day_complete``) so tests don't need to stub the
Twilio sender.
"""

from __future__ import annotations

from datetime import date

from app.db import InMemoryRepository, Player, ScoreRow
from app.notifications import (
    _classify,
    maybe_notify_day_complete,
    render_day_complete_summary,
    render_personal_best_message,
)

MON = date(2026, 4, 13)
TUE = date(2026, 4, 14)


class TestClassify:
    def test_no_history_returns_none(self):
        assert _classify([], 30) is None

    def test_strict_new_pb(self):
        assert _classify([30, 40, 50], 25) == "new_pb"

    def test_tied_pb(self):
        assert _classify([30, 40, 50], 30) == "tied_pb"

    def test_strict_new_worst(self):
        assert _classify([30, 40, 50], 60) == "new_worst"

    def test_tied_worst(self):
        assert _classify([30, 40, 50], 50) == "tied_worst"

    def test_middling_returns_none(self):
        # Between best (20) and worst (60), not matching either.
        assert _classify([20, 40, 60], 35) is None

    def test_all_identical_history_reads_as_tied_pb(self):
        # When every prior submission is the same, best == worst.
        # We lean positive and call a match a tied PB instead of a
        # tied worst so we don't mock a consistently-fine player.
        assert _classify([30, 30, 30], 30) == "tied_pb"


class TestRenderMessage:
    def test_new_pb_message_mentions_game_and_prior(self):
        body = render_personal_best_message(
            player_name="Alice",
            game="queens",
            new_raw=25,
            prior_raws=[30, 40],
        )
        assert body is not None
        assert "Queens" in body
        # Both the new and old times should be rendered as M:SS.
        assert "0:25" in body
        assert "0:30" in body

    def test_tied_pb_message_names_player(self):
        body = render_personal_best_message(
            player_name="Alice",
            game="queens",
            new_raw=30,
            prior_raws=[30, 40, 50],
        )
        assert body is not None
        assert "0:30" in body
        # Tied-PB copy should be celebratory-ish, not a roast.
        lowered = body.lower()
        assert "worst" not in lowered
        assert "yikes" not in lowered
        assert "woof" not in lowered

    def test_new_worst_message_calls_out_prior(self):
        body = render_personal_best_message(
            player_name="Alice",
            game="queens",
            new_raw=90,
            prior_raws=[30, 40, 60],
        )
        assert body is not None
        assert "Queens" in body
        assert "1:30" in body  # new worst formatted
        assert "1:00" in body  # prior worst (60s)

    def test_tied_worst_message(self):
        body = render_personal_best_message(
            player_name="Alice",
            game="queens",
            new_raw=60,
            prior_raws=[30, 40, 60],
        )
        assert body is not None
        assert "1:00" in body

    def test_middling_returns_none(self):
        assert (
            render_personal_best_message(
                player_name="Alice",
                game="queens",
                new_raw=35,
                prior_raws=[20, 40, 60],
            )
            is None
        )

    def test_pinpoint_formatted_as_guesses(self):
        # Pinpoint raw_score is a guess count, not seconds.
        body = render_personal_best_message(
            player_name="Alice",
            game="pinpoint",
            new_raw=1,
            prior_raws=[3, 5],
        )
        assert body is not None
        assert "1 guess" in body


# ---------------------------------------------------------------------------
# render_day_complete_summary + maybe_notify_day_complete
# ---------------------------------------------------------------------------


def _s(pid, name, game, pn, raw, d=TUE):
    return ScoreRow(pid, name, game, pn, d, raw)


class TestDayCompleteSummary:
    def test_renders_each_enabled_game_with_rank(self):
        alice = Player(id=1, whatsapp_id="whatsapp:+1", display_name="Alice")
        today_scores = [
            _s(1, "Alice", "queens", 714, 30),
            _s(2, "Bob",   "queens", 714, 50),
            _s(1, "Alice", "tango",  554, 40),
            _s(2, "Bob",   "tango",  554, 30),
        ]
        enabled = frozenset({"queens", "tango"})
        body = render_day_complete_summary(
            player=alice,
            today=TUE,
            today_scores=today_scores,
            week_scores=today_scores,
            enabled_games=enabled,
        )
        assert "Alice" in body
        assert "Queens" in body
        assert "Tango" in body
        # Alice wins Queens (rank 1/2), loses Tango (rank 2/2).
        assert "rank 1/2" in body
        assert "rank 2/2" in body
        # Weekly standing line.
        assert "currently" in body

    def test_pinpoint_renders_as_guesses(self):
        alice = Player(id=1, whatsapp_id="whatsapp:+1", display_name="Alice")
        today_scores = [_s(1, "Alice", "pinpoint", 714, 3)]
        enabled = frozenset({"pinpoint"})
        body = render_day_complete_summary(
            player=alice,
            today=TUE,
            today_scores=today_scores,
            week_scores=today_scores,
            enabled_games=enabled,
        )
        assert "3 guesses" in body


class TestMaybeNotifyDayComplete:
    def test_fires_when_every_game_done(self):
        repo = InMemoryRepository()
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=TUE, raw_score=30, share_text="",
        )
        repo.insert_score(
            player_id=alice.id, game="tango", puzzle_no=554,
            puzzle_date=TUE, raw_score=40, share_text="",
        )
        enabled = frozenset({"queens", "tango"})
        body = maybe_notify_day_complete(
            repo, None,
            player=alice,
            today=TUE,
            enabled_games=enabled,
        )
        assert body is not None
        assert "Alice" in body

    def test_silent_when_games_missing(self):
        repo = InMemoryRepository()
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=TUE, raw_score=30, share_text="",
        )
        # tango not yet submitted
        enabled = frozenset({"queens", "tango"})
        body = maybe_notify_day_complete(
            repo, None,
            player=alice,
            today=TUE,
            enabled_games=enabled,
        )
        assert body is None

    def test_silent_when_no_enabled_games(self):
        repo = InMemoryRepository()
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        body = maybe_notify_day_complete(
            repo, None,
            player=alice,
            today=TUE,
            enabled_games=frozenset(),
        )
        assert body is None
