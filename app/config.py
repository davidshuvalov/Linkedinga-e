"""Environment-driven settings for the app.

Reads from process env (and a local ``.env`` if ``python-dotenv`` is
installed). Missing values default to empty strings — the webhook falls
back to an in-memory repository when Supabase credentials aren't present,
so the app can still boot for local smoke testing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover — optional in CI / test envs
    pass


@dataclass(frozen=True)
class Settings:
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_whatsapp_from: str
    twilio_recap_to: str
    supabase_url: str
    supabase_key: str
    timezone_name: str
    enabled_games: frozenset

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)


_DEFAULT_ENABLED = "queens,tango,zip,patches,mini_sudoku"


def _env(name: str, default: str = "") -> str:
    """Read an env var, treating empty strings as unset.

    Railway / Docker setups often leave variables defined but empty, which
    makes ``os.environ.get(name, default)`` return ``""`` instead of the
    default. That's a latent crash for things like ``ZoneInfo("")``.
    """
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    return val


def load_settings() -> Settings:
    raw_games = _env("ENABLED_GAMES", _DEFAULT_ENABLED)
    enabled = frozenset(g.strip() for g in raw_games.split(",") if g.strip())
    return Settings(
        twilio_account_sid=_env("TWILIO_ACCOUNT_SID"),
        twilio_auth_token=_env("TWILIO_AUTH_TOKEN"),
        twilio_whatsapp_from=_env("TWILIO_WHATSAPP_FROM"),
        twilio_recap_to=_env("TWILIO_RECAP_TO"),
        supabase_url=_env("SUPABASE_URL"),
        supabase_key=_env("SUPABASE_KEY"),
        timezone_name=_env("APP_TIMEZONE", "Australia/Sydney"),
        enabled_games=enabled,
    )
