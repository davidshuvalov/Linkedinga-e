"""Environment-driven settings for the app.

Reads from process env (and a local ``.env`` if ``python-dotenv`` is
installed). Missing values default to empty strings — the webhook falls
back to an in-memory repository when Supabase credentials aren't present,
so the app can still boot for local smoke testing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover — optional in CI / test envs
    pass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_whatsapp_from: str
    twilio_recap_to: str
    twilio_status_callback_url: str
    supabase_url: str
    supabase_key: str
    timezone_name: str
    enabled_games: frozenset
    # Games the group plays but doesn't score. They hold the day open —
    # nothing is "done for today" until these are in — but they never
    # reach the leaderboard, points, prizes, or badges. See
    # :attr:`day_games`.
    wait_games: frozenset = frozenset()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def day_games(self) -> frozenset:
        """Every game that must be submitted before the day counts as
        finished — the scored games plus the waited-for ones.

        This is the completion set: the "day done" scorecard, the
        early-fire group recap, and the morning / pre-reset nags all
        ask "has this player played :attr:`day_games` yet?".
        :attr:`enabled_games` stays the *scoring* set, so a waited-for
        game delays the wrap without earning a single point.
        """
        return frozenset(self.enabled_games) | frozenset(self.wait_games)

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)


_DEFAULT_ENABLED = "queens,tango,zip,patches,mini_sudoku"


def _env(name: str, default: str = "") -> str:
    """Read an env var, treating empty/whitespace values as unset.

    Railway / Docker setups often leave variables defined but empty, which
    makes ``os.environ.get(name, default)`` return ``""`` instead of the
    default. That's a latent crash for things like ``ZoneInfo("")``. Also
    strips surrounding whitespace — copy-pasted values routinely pick up
    trailing newlines that break URL construction.
    """
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    return val.strip()


def _normalize_supabase_url(url: str) -> str:
    """Clean up common ``SUPABASE_URL`` copy-paste mistakes.

    The supabase-py client appends ``/rest/v1/<table>`` itself, so the
    URL must be the bare project origin (e.g. ``https://xxx.supabase.co``).
    Anything else produces ``PGRST125: Invalid path specified in request
    URL`` on every query. We defensively fix the most common errors:

    - trailing slashes (``https://xxx.supabase.co/``)
    - pasted-in REST suffix (``.../rest/v1`` or ``.../rest/v1/``)
    - stray whitespace (handled by :func:`_env` before this)
    """
    if not url:
        return url
    url = url.rstrip("/")
    # Strip either ``/rest/v1`` or ``/rest`` if pasted in.
    for suffix in ("/rest/v1", "/rest"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            break
    return url


def load_settings() -> Settings:
    raw_games = _env("ENABLED_GAMES", _DEFAULT_ENABLED)
    enabled = frozenset(g.strip() for g in raw_games.split(",") if g.strip())

    # Waited-for games are held out of scoring, so a game listed in both
    # lists is simply a tracked game — drop the overlap rather than let
    # the two settings contradict each other.
    raw_wait = _env("WAIT_FOR_GAMES", "")
    wait = frozenset(
        g.strip() for g in raw_wait.split(",") if g.strip()
    ) - enabled

    supabase_url = _normalize_supabase_url(_env("SUPABASE_URL"))
    if supabase_url and not supabase_url.startswith(("http://", "https://")):
        # Warn loudly — a bare hostname produces PGRST125 on every call.
        logger.warning(
            "SUPABASE_URL does not start with http(s)://; this will fail. "
            "Set it to the full project URL, e.g. https://<ref>.supabase.co"
        )

    return Settings(
        twilio_account_sid=_env("TWILIO_ACCOUNT_SID"),
        twilio_auth_token=_env("TWILIO_AUTH_TOKEN"),
        twilio_whatsapp_from=_env("TWILIO_WHATSAPP_FROM"),
        twilio_recap_to=_env("TWILIO_RECAP_TO"),
        twilio_status_callback_url=_env("TWILIO_STATUS_CALLBACK_URL"),
        supabase_url=supabase_url,
        supabase_key=_env("SUPABASE_KEY"),
        timezone_name=_env("APP_TIMEZONE", "Australia/Sydney"),
        enabled_games=enabled,
        wait_games=wait,
    )

