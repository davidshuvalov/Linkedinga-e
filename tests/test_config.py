"""Tests for ``app.config.load_settings``.

Covers the empty-string-env-var edge case that previously caused
``ZoneInfo("")`` to raise on every webhook call when Railway had
``APP_TIMEZONE=""`` defined.
"""

from __future__ import annotations

import pytest

from app.config import load_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip every env var the settings loader reads so each test starts clean."""
    for name in (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_WHATSAPP_FROM",
        "TWILIO_RECAP_TO",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "APP_TIMEZONE",
        "ENABLED_GAMES",
    ):
        monkeypatch.delenv(name, raising=False)


class TestEmptyEnvVars:
    def test_empty_app_timezone_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("APP_TIMEZONE", "")
        settings = load_settings()
        assert settings.timezone_name == "Australia/Sydney"
        # .tz must not raise
        assert settings.tz is not None

    def test_whitespace_app_timezone_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("APP_TIMEZONE", "   ")
        settings = load_settings()
        assert settings.timezone_name == "Australia/Sydney"

    def test_empty_enabled_games_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ENABLED_GAMES", "")
        settings = load_settings()
        assert "queens" in settings.enabled_games
        assert "tango" in settings.enabled_games

    def test_empty_supabase_creds_means_no_supabase(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "")
        monkeypatch.setenv("SUPABASE_KEY", "")
        settings = load_settings()
        assert settings.has_supabase is False

    def test_explicit_app_timezone_is_respected(self, monkeypatch):
        monkeypatch.setenv("APP_TIMEZONE", "America/New_York")
        settings = load_settings()
        assert settings.timezone_name == "America/New_York"
