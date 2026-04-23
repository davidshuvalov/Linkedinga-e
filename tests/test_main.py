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
from app.main import app, get_puzzle_validator, get_repository


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


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

    def test_random_chatter_gets_help_reply(self, client):
        # Bot now replies with a "didn't understand" blurb + command
        # list rather than staying silent — it operates in 1:1 DMs
        # so users were being left guessing.
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
        assert "didn't understand" in msg
        assert "stats" in msg

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
