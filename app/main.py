"""FastAPI app — Twilio WhatsApp webhook + scheduled recap/wrap jobs.

Routes:

- ``GET  /health`` — liveness probe for Railway / uptime checks.
- ``POST /webhook`` — Twilio WhatsApp webhook. Accepts the form-encoded
  payload, dispatches to :func:`app.webhook.handle_inbound`, and returns
  a TwiML response that Twilio relays back to the sender.

Scheduled jobs (APScheduler, ``Australia/Sydney``):

- **Daily recap** — every day at 21:00
- **Weekly wrap** — every Sunday at 20:00

Run locally::

    uvicorn app.main:app --reload

With no Supabase credentials configured the app falls back to an
in-memory repository so you can smoke-test the webhook end-to-end.
Scheduled jobs still fire but print to stdout instead of calling Twilio
when ``TWILIO_ACCOUNT_SID`` is not set.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

from fastapi import Depends, FastAPI, Form, Response

from .config import Settings, load_settings
from .db import InMemoryRepository, Repository, SupabaseRepository
from .webhook import handle_inbound

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Repository singleton
# ---------------------------------------------------------------------------

_repo_singleton: Repository | None = None


def get_repository() -> Repository:
    """FastAPI dependency that returns a Repository.

    Overridable in tests via ``app.dependency_overrides[get_repository]``.
    """
    global _repo_singleton
    if _repo_singleton is not None:
        return _repo_singleton
    settings = load_settings()
    if settings.has_supabase:
        from supabase import create_client  # type: ignore

        client = create_client(settings.supabase_url, settings.supabase_key)
        _repo_singleton = SupabaseRepository(client)
    else:
        _repo_singleton = InMemoryRepository()
    return _repo_singleton


def get_settings() -> Settings:
    """FastAPI dependency returning the current :class:`Settings`."""
    return load_settings()


# ---------------------------------------------------------------------------
# APScheduler setup
# ---------------------------------------------------------------------------


def _setup_scheduler() -> None:
    """Create and start a BackgroundScheduler with the daily recap and
    weekly wrap cron triggers.

    Runs inside the FastAPI lifespan so the scheduler starts after the
    app boots and shuts down when the app stops. Imported lazily so that
    tests that don't need the scheduler aren't slowed by the import.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    from .jobs import run_daily_recap, run_weekly_wrap

    settings = load_settings()
    tz_name = settings.timezone_name

    scheduler = BackgroundScheduler()

    def _daily():
        run_daily_recap(get_repository(), settings)

    def _weekly():
        run_weekly_wrap(get_repository(), settings)

    scheduler.add_job(
        _daily,
        CronTrigger(hour=21, minute=0, timezone=tz_name),
        id="daily_recap",
        replace_existing=True,
    )
    scheduler.add_job(
        _weekly,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=tz_name),
        id="weekly_wrap",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started: daily_recap at 21:00 %s, "
        "weekly_wrap Sun 20:00 %s",
        tz_name,
        tz_name,
    )
    return scheduler


# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = _setup_scheduler()
    yield
    scheduler.shutdown(wait=False)
    logger.info("Scheduler shut down.")


# ---------------------------------------------------------------------------
# App + routes
# ---------------------------------------------------------------------------

app = FastAPI(
    title="LinkedIn Games WhatsApp Score Tracker",
    lifespan=lifespan,
)


def _twiml(reply_text: str) -> str:
    """Wrap ``reply_text`` in a minimal TwiML ``<Response><Message>`` envelope."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Message>{xml_escape(reply_text)}</Message></Response>"
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(
    from_: str = Form(..., alias="From"),
    body: str = Form("", alias="Body"),
    profile_name: str = Form("", alias="ProfileName"),
    repo: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> Response:
    reply = handle_inbound(
        repo,
        from_=from_,
        body=body,
        profile_name=profile_name,
        now=datetime.now(settings.tz),
    )
    return Response(content=_twiml(reply), media_type="application/xml")
