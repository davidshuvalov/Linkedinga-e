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

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)


def load_settings() -> Settings:
    return Settings(
        twilio_account_sid=os.environ.get("TWILIO_ACCOUNT_SID", ""),
        twilio_auth_token=os.environ.get("TWILIO_AUTH_TOKEN", ""),
        twilio_whatsapp_from=os.environ.get("TWILIO_WHATSAPP_FROM", ""),
        twilio_recap_to=os.environ.get("TWILIO_RECAP_TO", ""),
        supabase_url=os.environ.get("SUPABASE_URL", ""),
        supabase_key=os.environ.get("SUPABASE_KEY", ""),
        timezone_name=os.environ.get("APP_TIMEZONE", "Australia/Sydney"),
    )
