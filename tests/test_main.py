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
from app.main import app, get_repository


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def client(repo: InMemoryRepository):
    app.dependency_overrides[get_repository] = lambda: repo
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
        # Bot replies explaining it couldn't parse
        root = ET.fromstring(r.text)
        assert "couldn't parse" in (root.find("Message").text or "").lower()

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

    def test_missing_body_field_is_allowed(self, client):
        r = client.post(
            "/webhook",
            data={"From": "whatsapp:+61400000001", "ProfileName": "Alice"},
        )
        assert r.status_code == 200
        assert "<Response>" in r.text

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
