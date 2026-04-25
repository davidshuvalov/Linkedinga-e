"""Unit tests for ``app.notifications`` — submission triggers, the
PB / worst-ever DM body, and the "day complete" personal summary.

Focuses on the pure-function cores (the trigger detectors,
``render_personal_best_message``, ``render_day_complete_summary``,
``maybe_notify_day_complete``) so tests don't need to stub the
Twilio sender.
"""

from __future__ import annotations

from datetime import date

from app.db import InMemoryRepository, Player, ScoreRow
from app.notifications import (
    _detect_personal_history_trigger,
    gather_submission_triggers,
    maybe_notify_day_complete,
    maybe_notify_personal_best,
    render_day_complete_summary,
    render_personal_best_message,
)

MON = date(2026, 4, 13)
TUE = date(2026, 4, 14)


class TestPersonalHistoryTriggerDetection:
    """``_detect_personal_history_trigger`` is the pure-function core
    of the PB / tied-PB / personal-worst / tied-worst classification.
    Returns a :class:`Trigger` whose ``kind`` slots into the template
    pools, or ``None`` for middling scores with nothing to celebrate
    or roast."""

    def test_no_history_returns_none(self):
        assert _detect_personal_history_trigger([], 30) is None

    def test_strict_new_pb(self):
        t = _detect_personal_history_trigger([30, 40, 50], 25)
        assert t is not None and t.kind == "new_pb"

    def test_tied_pb(self):
        t = _detect_personal_history_trigger([30, 40, 50], 30)
        assert t is not None and t.kind == "tied_pb"

    def test_strict_new_worst(self):
        t = _detect_personal_history_trigger([30, 40, 50], 60)
        assert t is not None and t.kind == "new_worst"

    def test_tied_worst(self):
        t = _detect_personal_history_trigger([30, 40, 50], 50)
        assert t is not None and t.kind == "tied_worst"

    def test_middling_returns_none(self):
        # Between best (20) and worst (60), not matching either.
        assert _detect_personal_history_trigger([20, 40, 60], 35) is None

    def test_all_identical_history_reads_as_tied_pb(self):
        # When every prior submission is the same, best == worst.
        # We lean positive and call a match a tied PB instead of a
        # tied worst so we don't mock a consistently-fine player.
        t = _detect_personal_history_trigger([30, 30, 30], 30)
        assert t is not None and t.kind == "tied_pb"


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


class TestGatherSubmissionTriggers:
    """``gather_submission_triggers`` runs every detector against the
    just-inserted submission and returns the union of triggers that
    fired. Caller is responsible for picking one (random or otherwise).
    Tests use the in-memory repo so the queries are deterministic."""

    def _seed(self, repo, player_id, name, game, puzzle_no, raw, day=TUE):
        # ``player_id`` is the suffix on the synthetic whatsapp_id so
        # the same name keeps the same identity across calls; the
        # actual repo-assigned id comes back from get_or_create_player.
        player = repo.get_or_create_player(
            f"whatsapp:+6140000000{player_id}", name
        )
        repo.insert_score(
            player_id=player.id, game=game, puzzle_no=puzzle_no,
            puzzle_date=day, raw_score=raw, share_text="x",
        )
        return player.id

    def test_first_ever_submission_fires_only_first_today(self):
        # Solo player, first-ever submission, no field, no history.
        # PB / today-extreme / all-time-record / dow / year detectors
        # all bow out (they need a comparison point); ``first_today``
        # is the only trigger that legitimately fires for a brand-
        # new game in a brand-new group.
        repo = InMemoryRepository()
        alice_id = self._seed(repo, 1, "Alice", "queens", 714, 30)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=30, today=TUE, prior_raws=[],
        )
        kinds = {t.kind for t in triggers}
        assert kinds == {"first_today"}

    def test_personal_pb_fires(self):
        repo = InMemoryRepository()
        alice_id = self._seed(repo, 1, "Alice", "queens", 713, 25, day=MON)
        self._seed(repo, 1, "Alice", "queens", 714, 20)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=20, today=TUE, prior_raws=[25],
        )
        kinds = {t.kind for t in triggers}
        assert "new_pb" in kinds

    def test_best_of_day_fires_when_player_beats_field(self):
        # Bob already played today (30s), Alice just put down 20s.
        # Alice's submission is best of today.
        repo = InMemoryRepository()
        self._seed(repo, 2, "Bob", "queens", 714, 30)
        alice_id = self._seed(repo, 1, "Alice", "queens", 714, 20)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=20, today=TUE, prior_raws=[],
        )
        kinds = {t.kind for t in triggers}
        assert "best_of_day" in kinds

    def test_worst_of_day_fires_when_player_lags_field(self):
        repo = InMemoryRepository()
        self._seed(repo, 2, "Bob", "queens", 714, 30)
        alice_id = self._seed(repo, 1, "Alice", "queens", 714, 90)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=90, today=TUE, prior_raws=[],
        )
        kinds = {t.kind for t in triggers}
        assert "worst_of_day" in kinds

    def test_no_today_extreme_when_alone(self):
        # Only Alice has played queens today; "best of day" doesn't
        # fire because there's no field to beat.
        repo = InMemoryRepository()
        alice_id = self._seed(repo, 1, "Alice", "queens", 714, 20)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=20, today=TUE, prior_raws=[],
        )
        kinds = {t.kind for t in triggers}
        assert "best_of_day" not in kinds
        assert "worst_of_day" not in kinds

    def test_all_time_record_fires_when_runner_up_exists(self):
        # Bob held the all-time record at 30s; Alice just hit 20s.
        # Alice's submission is the new all-time record; Bob's 30s
        # is the runner-up that gets credited as the prior holder.
        repo = InMemoryRepository()
        self._seed(repo, 2, "Bob", "queens", 700, 30, day=MON)
        alice_id = self._seed(repo, 1, "Alice", "queens", 714, 20)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=20, today=TUE, prior_raws=[],
        )
        kinds = {t.kind for t in triggers}
        assert "all_time_record" in kinds
        record = next(t for t in triggers if t.kind == "all_time_record")
        assert record.format_data["prior_holder"] == "Bob"

    def test_all_time_anti_record_fires(self):
        # Bob's 30s was the all-time worst; Alice now puts up 90s.
        repo = InMemoryRepository()
        self._seed(repo, 2, "Bob", "queens", 700, 30, day=MON)
        alice_id = self._seed(repo, 1, "Alice", "queens", 714, 90)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=90, today=TUE, prior_raws=[],
        )
        kinds = {t.kind for t in triggers}
        assert "all_time_anti_record" in kinds

    def test_multiple_triggers_can_fire_simultaneously(self):
        # Alice's 20s is BOTH her PB AND best of today AND a new
        # all-time record. The gatherer should return all three,
        # leaving the random pick to the caller.
        repo = InMemoryRepository()
        alice_id = self._seed(
            repo, 1, "Alice", "queens", 700, 30, day=MON
        )  # Alice's old PB
        self._seed(repo, 2, "Bob", "queens", 714, 25)  # today's prior leader
        self._seed(repo, 1, "Alice", "queens", 714, 20)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=20, today=TUE, prior_raws=[30],
        )
        kinds = {t.kind for t in triggers}
        assert "new_pb" in kinds
        assert "best_of_day" in kinds
        assert "all_time_record" in kinds


class TestPhaseDTriggers:
    """Phase D adds finer slices of personal history:
    day-of-week PB / day-of-week worst / year PB / first-of-the-day.
    These detectors operate on the player's own game history rather
    than the cross-player snapshot used by the Pass A detectors."""

    def _seed(self, repo, player_id, name, game, puzzle_no, raw, day=TUE):
        player = repo.get_or_create_player(
            f"whatsapp:+6140000000{player_id}", name
        )
        repo.insert_score(
            player_id=player.id, game=game, puzzle_no=puzzle_no,
            puzzle_date=day, raw_score=raw, share_text="x",
        )
        return player.id

    def test_dow_pb_fires_on_matching_weekday(self):
        repo = InMemoryRepository()
        # Tuesday Apr 14, prior Tuesdays: Apr 7 with 30s.
        prev_tue = date(2026, 4, 7)
        self._seed(repo, 1, "Alice", "queens", 700, 30, day=prev_tue)
        alice_id = self._seed(repo, 1, "Alice", "queens", 714, 20, day=TUE)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=20, today=TUE, prior_raws=[30],
        )
        kinds = {t.kind for t in triggers}
        assert "dow_pb" in kinds
        dow = next(t for t in triggers if t.kind == "dow_pb")
        assert dow.format_data["weekday"] == "Tuesday"

    def test_dow_pb_silent_when_no_prior_same_weekday(self):
        # Played on a Monday; today is Tuesday — no prior Tuesday to
        # compare against. dow_pb should not fire (year_pb / new_pb
        # might still, that's fine).
        repo = InMemoryRepository()
        self._seed(repo, 1, "Alice", "queens", 700, 30, day=MON)
        alice_id = self._seed(repo, 1, "Alice", "queens", 714, 20, day=TUE)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=20, today=TUE, prior_raws=[30],
        )
        kinds = {t.kind for t in triggers}
        assert "dow_pb" not in kinds

    def test_dow_worst_fires(self):
        repo = InMemoryRepository()
        prev_tue = date(2026, 4, 7)
        self._seed(repo, 1, "Alice", "queens", 700, 30, day=prev_tue)
        alice_id = self._seed(repo, 1, "Alice", "queens", 714, 80, day=TUE)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=80, today=TUE, prior_raws=[30],
        )
        kinds = {t.kind for t in triggers}
        assert "dow_worst" in kinds

    def test_year_pb_fires(self):
        repo = InMemoryRepository()
        # Earlier in the same year on a different weekday so the
        # year detector gets to run alone (not co-firing with
        # dow_pb).
        self._seed(repo, 1, "Alice", "queens", 700, 30, day=date(2026, 1, 5))
        alice_id = self._seed(repo, 1, "Alice", "queens", 714, 20, day=TUE)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=20, today=TUE, prior_raws=[30],
        )
        kinds = {t.kind for t in triggers}
        assert "year_pb" in kinds
        year_t = next(t for t in triggers if t.kind == "year_pb")
        assert year_t.format_data["year"] == "2026"

    def test_year_pb_silent_when_no_prior_in_year(self):
        # First-ever submission — no prior in the year to PB against.
        repo = InMemoryRepository()
        alice_id = self._seed(repo, 1, "Alice", "queens", 714, 20, day=TUE)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=20, today=TUE, prior_raws=[],
        )
        kinds = {t.kind for t in triggers}
        assert "year_pb" not in kinds

    def test_first_today_fires_when_only_score(self):
        repo = InMemoryRepository()
        alice_id = self._seed(repo, 1, "Alice", "queens", 714, 20, day=TUE)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=20, today=TUE, prior_raws=[],
        )
        kinds = {t.kind for t in triggers}
        assert "first_today" in kinds

    def test_first_today_silent_when_field_exists(self):
        # Bob already played; Alice's submission isn't the first.
        repo = InMemoryRepository()
        self._seed(repo, 2, "Bob", "queens", 714, 30, day=TUE)
        alice_id = self._seed(repo, 1, "Alice", "queens", 714, 20, day=TUE)
        triggers = gather_submission_triggers(
            repo, player_id=alice_id, game="queens",
            new_raw=20, today=TUE, prior_raws=[],
        )
        kinds = {t.kind for t in triggers}
        assert "first_today" not in kinds


class TestMaybeNotifyPersonalBestIntegration:
    """End-to-end smoke test for the orchestration entry point: it
    should pick one trigger at random and return its rendered body
    when at least one fires."""

    def test_returns_body_when_any_trigger_fires(self):
        repo = InMemoryRepository()
        alice = repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        bob = repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        # Bob's 30s is the prior all-time fastest; Alice just hit 20s.
        repo.insert_score(
            player_id=bob.id, game="queens", puzzle_no=700,
            puzzle_date=MON, raw_score=30, share_text="x",
        )
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=TUE, raw_score=20, share_text="x",
        )
        body = maybe_notify_personal_best(
            repo, None,
            player=alice,
            game="queens",
            new_raw=20,
            today=TUE,
        )
        assert body is not None
        assert "Alice" in body
        assert "Queens" in body

    def test_returns_none_when_truly_nothing_to_celebrate(self):
        # Middling submission for a player with prior history but
        # not extreme on any axis: not a PB, not a worst, not the
        # day's best, not the day's worst (other players bracket
        # Alice today), not an all-time record, not first-of-day,
        # not a Tuesday extreme (prior Tuesday brackets her 40).
        repo = InMemoryRepository()
        alice = repo.get_or_create_player("whatsapp:+61400000001", "Alice")
        bob = repo.get_or_create_player("whatsapp:+61400000002", "Bob")
        charlie = repo.get_or_create_player("whatsapp:+61400000003", "Charlie")
        # Alice's history: prior Tuesdays bracket her 40 (20 last
        # Tue, 70 the Tuesday before) and a Monday past the 40 ceiling
        # (60). 40 ends up middling on every axis: not a PB, not a
        # personal worst, not a Tuesday-extreme, not a year-extreme.
        last_tue = date(2026, 4, 7)
        prior_tue = date(2026, 3, 31)
        last_mon = date(2026, 4, 6)
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=700,
            puzzle_date=last_tue, raw_score=20, share_text="x",
        )
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=693,
            puzzle_date=prior_tue, raw_score=70, share_text="x",
        )
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=699,
            puzzle_date=last_mon, raw_score=60, share_text="x",
        )
        # Bob and Charlie bracket Alice on today's field.
        repo.insert_score(
            player_id=bob.id, game="queens", puzzle_no=714,
            puzzle_date=TUE, raw_score=15, share_text="x",
        )
        repo.insert_score(
            player_id=charlie.id, game="queens", puzzle_no=714,
            puzzle_date=TUE, raw_score=70, share_text="x",
        )
        repo.insert_score(
            player_id=alice.id, game="queens", puzzle_no=714,
            puzzle_date=TUE, raw_score=40, share_text="x",
        )
        body = maybe_notify_personal_best(
            repo, None,
            player=alice,
            game="queens",
            new_raw=40,
            today=TUE,
        )
        assert body is None


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
