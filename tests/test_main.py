"""Integration tests for ``app.main`` via FastAPI's TestClient.

These exercise the wiring — form-encoded payload → ``handle_inbound`` →
TwiML reply — with a fake in-memory repository injected via
``app.dependency_overrides``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app.db import InMemoryRepository

from .conftest import TestRepo
from app.main import app, get_puzzle_validator, get_repository


@pytest.fixture
def repo() -> InMemoryRepository:
    return TestRepo()


@pytest.fixture
def client(repo: InMemoryRepository):
    # Disable puzzle-number validation for the default fixture — existing
    # tests submit arbitrary puzzle numbers (#1, #365 etc.) that would
    # otherwise be rejected because they don't match today's LinkedIn number.
    # The dedicated TestPuzzleValidation class re-enables validation.
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_puzzle_validator] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /webhook
# ---------------------------------------------------------------------------


class TestWebhook:
    def test_parses_and_persists_queens_score(self, client, repo):
        r = client.post(
            "/webhook",
            data={
                "From": "whatsapp:+61400000001",
                "Body": "Queens #365 | 1:23",
                "ProfileName": "Alice",
            },
        )
        assert r.status_code == 200
        assert "application/xml" in r.headers["content-type"]
        assert "<Response>" in r.text
        assert "<Message>" in r.text
        assert "Queens" in r.text
        assert "#365" in r.text
        assert "1:23" in r.text

        assert len(repo.scores) == 1
        assert repo.scores[0]["game"] == "queens"
        assert repo.scores[0]["puzzle_no"] == 365
        assert repo.scores[0]["raw_score"] == 83

    def test_response_is_well_formed_xml_even_with_ampersand_in_name(
        self, client
    ):
        r = client.post(
            "/webhook",
            data={
                "From": "whatsapp:+61400000001",
                "Body": "Queens #1 | 0:30",
                "ProfileName": "Al & ice",
            },
        )
        root = ET.fromstring(r.text)
        assert root.tag == "Response"
        message_el = root.find("Message")
        assert message_el is not None
        assert message_el.text is not None
        assert "Al & ice" in message_el.text

    def test_unparseable_gameish_is_logged_to_unparsed(self, client, repo):
        r = client.post(
            "/webhook",
            data={
                "From": "whatsapp:+61400000001",
                "Body": "Queens today was a nightmare lnkd.in/queens",
                "ProfileName": "Alice",
            },
        )
        assert r.status_code == 200
        assert len(repo.unparsed) == 1
        assert len(repo.scores) == 0
        # Bot replies explaining it couldn't read the share
        root = ET.fromstring(r.text)
        assert "couldn't read" in (root.find("Message").text or "").lower()

    def test_duplicate_submission_does_not_double_insert(self, client, repo):
        data = {
            "From": "whatsapp:+61400000001",
            "Body": "Queens #365 | 1:23",
            "ProfileName": "Alice",
        }
        first = client.post("/webhook", data=data)
        second = client.post("/webhook", data=data)

        assert first.status_code == 200
        assert second.status_code == 200
        assert len(repo.scores) == 1
        assert "already" in second.text.lower()

    def test_missing_body_field_returns_silent_twiml(self, client):
        # Empty body is plain chatter → bot stays silent → bare <Response/>.
        r = client.post(
            "/webhook",
            data={"From": "whatsapp:+61400000001", "ProfileName": "Alice"},
        )
        assert r.status_code == 200
        assert "<Response/>" in r.text
        assert "<Message>" not in r.text

    def test_random_chatter_gets_short_pointer(self, client):
        # Bot replies with a short "send `help` for commands" rather
        # than dumping the full help blurb every time.
        r = client.post(
            "/webhook",
            data={
                "From": "whatsapp:+61400000001",
                "Body": "hey what's for dinner",
                "ProfileName": "Alice",
            },
        )
        assert r.status_code == 200
        assert "<Message>" in r.text
        root = ET.fromstring(r.text)
        msg = (root.find("Message").text or "").lower()
        assert "don't understand" in msg
        assert "help" in msg
        # Help blurb itself should NOT be inlined.
        assert "stats" not in msg

    def test_missing_from_is_422(self, client):
        r = client.post(
            "/webhook",
            data={"Body": "Queens #1 | 0:30", "ProfileName": "Alice"},
        )
        assert r.status_code == 422

    def test_pinpoint_round_trip(self, client, repo):
        r = client.post(
            "/webhook",
            data={
                "From": "whatsapp:+61400000002",
                "Body": "Pinpoint #7 | 2 guesses",
                "ProfileName": "Bob",
            },
        )
        assert r.status_code == 200
        assert "2 guesses" in r.text
        assert repo.scores[0]["game"] == "pinpoint"
        assert repo.scores[0]["raw_score"] == 2


# ---------------------------------------------------------------------------
# Long-message chunking — WhatsApp's 1600-char per-Message ceiling
# silently drops oversized bodies, which is what produced the
# "after submit, recap/today outputs nothing" report on the last day
# of the month (recap balloons with month totals + game winners +
# prizes). _twiml chunks long bodies into multiple <Message> verbs
# inside one <Response>.
# ---------------------------------------------------------------------------


class TestTwimlChunking:
    def test_short_body_emits_single_message(self):
        from app.main import _twiml

        out = _twiml("hello")
        assert out.count("<Message>") == 1
        assert "<Message>hello</Message>" in out

    def test_none_body_emits_silent_response(self):
        from app.main import _twiml

        out = _twiml(None)
        assert "<Response/>" in out
        assert "<Message>" not in out

    def test_oversized_body_splits_at_paragraph_boundaries(self):
        from app.main import _MAX_MESSAGE_CHARS, _chunk_message, _twiml

        # Mimic recap shape: paragraphs separated by blank lines.
        para = "Queens #714\n  Alice — 0:10 (5 pts)\n  Bob — 0:20 (4 pts)"
        body = "\n\n".join([para] * 80)
        assert len(body) > _MAX_MESSAGE_CHARS

        chunks = _chunk_message(body)
        assert len(chunks) >= 2
        assert all(len(c) <= _MAX_MESSAGE_CHARS for c in chunks)
        # Joining chunks back with paragraph breaks recovers the body
        # — guarantees no content was dropped.
        assert "\n\n".join(chunks) == body

        out = _twiml(body)
        assert out.count("<Message>") == len(chunks)
        # Every chunk lands inside a <Message>...</Message> envelope.
        for chunk in chunks:
            assert (
                f"<Message>{chunk}</Message>".replace("&", "&amp;")
                in out.replace("&", "&amp;")
            )

    def test_oversized_paragraph_falls_back_to_line_split(self):
        from app.main import _MAX_MESSAGE_CHARS, _chunk_message

        # One giant paragraph (no \n\n) that's way over the limit but
        # has line breaks. Must still split rather than emit one
        # oversized chunk.
        line = "x" * 100
        body = "\n".join([line] * 30)  # 30*100 + 29 newlines ~= 3029 chars
        assert len(body) > _MAX_MESSAGE_CHARS
        assert "\n\n" not in body

        chunks = _chunk_message(body)
        assert len(chunks) >= 2
        assert all(len(c) <= _MAX_MESSAGE_CHARS for c in chunks)

    def test_recap_response_under_limit_per_chunk(self, client, repo):
        # End-to-end: drive the full recap on the last day of the month
        # through the FastAPI webhook and confirm every <Message> chunk
        # lands under WhatsApp's per-message ceiling.
        from datetime import date, timedelta
        from app.main import _MAX_MESSAGE_CHARS, get_settings
        from app.config import Settings
        from app.puzzles import la_date

        settings = Settings(
            twilio_account_sid="", twilio_auth_token="",
            twilio_whatsapp_from="", twilio_recap_to="",
            supabase_url="", supabase_key="",
            timezone_name="Australia/Sydney",
            enabled_games=frozenset(
                {"queens", "tango", "zip", "patches", "mini_sudoku"}
            ),
        )
        app.dependency_overrides[get_settings] = lambda: settings

        # Pick a NOW that lands on the last day of an LA month so the
        # recap appends month totals + game winners + prizes — the
        # exact shape that overflowed in production.
        from datetime import datetime
        from zoneinfo import ZoneInfo
        SYDNEY = ZoneInfo("Australia/Sydney")
        # Apr 30 2026 in LA == Apr 30/May 1 in Sydney; pick a Sydney
        # time that's still Apr 30 LA.
        last_day_now = datetime(2026, 5, 1, 0, 30, tzinfo=SYDNEY)
        today = la_date(last_day_now)
        assert today == date(2026, 4, 30)

        # Six players, week of scores; four play today.
        players = [
            (f"whatsapp:+614000000{i:02d}", name)
            for i, name in enumerate(
                ["Duviuvi1", "Alice", "Bob", "Charlie", "Dave", "Eve"], start=1
            )
        ]
        for wid, name in players:
            repo.get_or_create_player(wid, name)
        for d in range(7, 0, -1):
            day = today - timedelta(days=d)
            for pid, _ in enumerate(players, start=1):
                for game in sorted(settings.enabled_games):
                    repo.insert_score(
                        player_id=pid, game=game, puzzle_no=700 - d * 7 + pid,
                        puzzle_date=day, raw_score=20 + pid * 10 + d,
                        share_text=f"{game} d{d}",
                    )
        for pid, _ in enumerate(players[:4], start=1):
            for game in sorted(settings.enabled_games):
                repo.insert_score(
                    player_id=pid, game=game, puzzle_no=900 + pid,
                    puzzle_date=today, raw_score=30 + pid * 15,
                    share_text=f"{game} today",
                )

        # Patch datetime.now in app.main to return the last-day instant
        # so the webhook routes through the month-end recap path.
        import app.main as main_mod
        real_datetime = main_mod.datetime

        class _FakeDateTime:
            @staticmethod
            def now(tz=None):
                return last_day_now if tz is None else last_day_now.astimezone(tz)

        main_mod.datetime = _FakeDateTime
        try:
            r = client.post(
                "/webhook",
                data={
                    "From": players[0][0],
                    "Body": "today",
                    "ProfileName": "Duviuvi1",
                },
            )
        finally:
            main_mod.datetime = real_datetime

        assert r.status_code == 200
        # Bot must reply (not silent), and every chunk must fit under
        # the per-message ceiling so Twilio doesn't drop it.
        assert "<Message>" in r.text
        root = ET.fromstring(r.text)
        messages = root.findall("Message")
        assert len(messages) >= 1
        for msg in messages:
            assert msg.text is not None
            assert len(msg.text) <= _MAX_MESSAGE_CHARS
        # Concatenated body should contain the recap header — proves
        # we didn't lose content while chunking.
        joined = "\n\n".join(m.text for m in messages)
        assert "Daily recap" in joined
        assert "Month totals" in joined  # Apr 30 → month-end appendage


# ---------------------------------------------------------------------------
# Error handling — webhook must always return TwiML so Twilio can relay a
# reply to the sender. A 500 produces silence on WhatsApp + a red error in
# the Twilio console, which is the exact symptom we're guarding against.
# ---------------------------------------------------------------------------


class _ExplodingRepo:
    """Repository double whose every method raises."""

    def get_or_create_player(self, *args, **kwargs):
        raise RuntimeError("supabase blew up")

    def insert_score(self, *args, **kwargs):
        raise RuntimeError("supabase blew up")

    def log_unparsed(self, *args, **kwargs):
        raise RuntimeError("supabase blew up")

    def list_scores(self, *args, **kwargs):
        raise RuntimeError("supabase blew up")

    def list_active_whatsapp_ids(self, *args, **kwargs):
        raise RuntimeError("supabase blew up")

    def get_existing_score(self, *args, **kwargs):
        raise RuntimeError("supabase blew up")

    def list_player_scores(self, *args, **kwargs):
        raise RuntimeError("supabase blew up")

    def list_recent_unparsed(self, *args, **kwargs):
        raise RuntimeError("supabase blew up")


class TestWebhookErrorHandling:
    def test_repo_exception_returns_twiml_not_500(self):
        app.dependency_overrides[get_repository] = lambda: _ExplodingRepo()
        # Disable puzzle validation so the test exercises the repo path.
        app.dependency_overrides[get_puzzle_validator] = lambda: None
        try:
            client = TestClient(app)
            r = client.post(
                "/webhook",
                data={
                    "From": "whatsapp:+61400000001",
                    "Body": "Queens #365 | 1:23",
                    "ProfileName": "Alice",
                },
            )
            assert r.status_code == 200
            assert "application/xml" in r.headers["content-type"]
            root = ET.fromstring(r.text)
            assert root.tag == "Response"
            msg = root.find("Message")
            assert msg is not None and msg.text
            assert "error" in msg.text.lower()
        finally:
            app.dependency_overrides.clear()

    def test_unparsed_command_exception_returns_twiml(self):
        app.dependency_overrides[get_repository] = lambda: _ExplodingRepo()
        app.dependency_overrides[get_puzzle_validator] = lambda: None
        try:
            client = TestClient(app)
            r = client.post(
                "/webhook",
                data={
                    "From": "whatsapp:+61400000001",
                    "Body": "unparsed",
                    "ProfileName": "Alice",
                },
            )
            assert r.status_code == 200
            root = ET.fromstring(r.text)
            assert root.tag == "Response"
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Puzzle-number validation — webhook must reject yesterday's or tomorrow's
# puzzles so the leaderboard only contains LinkedIn's currently-live round.
# ---------------------------------------------------------------------------


class TestPuzzleValidation:
    def _client_with_validator(self, repo, validator):
        app.dependency_overrides[get_repository] = lambda: repo
        app.dependency_overrides[get_puzzle_validator] = lambda: validator
        return TestClient(app)

    def test_matching_puzzle_is_accepted(self, repo):
        client = self._client_with_validator(repo, lambda game, now: 721)
        try:
            r = client.post(
                "/webhook",
                data={
                    "From": "whatsapp:+61400000001",
                    "Body": "Queens #721\n1:05",
                    "ProfileName": "Alice",
                },
            )
            assert r.status_code == 200
            assert "Got it" in r.text
            assert len(repo.scores) == 1
        finally:
            app.dependency_overrides.clear()

    def test_stale_puzzle_is_rejected_and_not_stored(self, repo):
        client = self._client_with_validator(repo, lambda game, now: 721)
        try:
            r = client.post(
                "/webhook",
                data={
                    "From": "whatsapp:+61400000001",
                    "Body": "Queens #720\n1:05",
                    "ProfileName": "Alice",
                },
            )
            assert r.status_code == 200
            assert "#720" in r.text
            assert "#721" in r.text
            assert "yesterday" in r.text.lower()
            assert len(repo.scores) == 0
        finally:
            app.dependency_overrides.clear()

    def test_future_puzzle_is_rejected(self, repo):
        client = self._client_with_validator(repo, lambda game, now: 721)
        try:
            r = client.post(
                "/webhook",
                data={
                    "From": "whatsapp:+61400000001",
                    "Body": "Queens #722\n1:05",
                    "ProfileName": "Alice",
                },
            )
            assert r.status_code == 200
            assert "tomorrow" in r.text.lower()
            assert len(repo.scores) == 0
        finally:
            app.dependency_overrides.clear()
