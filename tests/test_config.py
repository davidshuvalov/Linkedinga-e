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


class TestSupabaseUrlNormalization:
    """Supabase PGRST125 ('Invalid path specified in request URL') is
    overwhelmingly caused by a malformed SUPABASE_URL. Defensively normalize
    so common copy-paste mistakes don't break every webhook call."""

    def test_trailing_slash_is_stripped(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co/")
        monkeypatch.setenv("SUPABASE_KEY", "anon-key")
        settings = load_settings()
        assert settings.supabase_url == "https://xyz.supabase.co"

    def test_rest_v1_suffix_is_stripped(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co/rest/v1")
        monkeypatch.setenv("SUPABASE_KEY", "anon-key")
        settings = load_settings()
        assert settings.supabase_url == "https://xyz.supabase.co"

    def test_rest_v1_with_trailing_slash_is_stripped(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co/rest/v1/")
        monkeypatch.setenv("SUPABASE_KEY", "anon-key")
        settings = load_settings()
        assert settings.supabase_url == "https://xyz.supabase.co"

    def test_whitespace_is_stripped(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "  https://xyz.supabase.co  \n")
        monkeypatch.setenv("SUPABASE_KEY", "anon-key")
        settings = load_settings()
        assert settings.supabase_url == "https://xyz.supabase.co"

    def test_clean_url_is_unchanged(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "anon-key")
        settings = load_settings()
        assert settings.supabase_url == "https://xyz.supabase.co"

    def test_missing_scheme_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("SUPABASE_URL", "xyz.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "anon-key")
        with caplog.at_level("WARNING", logger="app.config"):
            load_settings()
        assert any(
            "does not start with http" in record.message for record in caplog.records
        )
