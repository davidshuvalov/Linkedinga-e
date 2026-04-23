"""Unit tests for ``app.notifications`` — PB / worst-ever DM logic.

Focuses on :func:`render_personal_best_message` (the pure-function
classifier) so tests don't need to stub the Twilio sender. The
webhook-wiring path is covered by :mod:`tests.test_webhook_pb`.
"""

from __future__ import annotations

from app.notifications import (
    _classify,
    render_personal_best_message,
)


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
