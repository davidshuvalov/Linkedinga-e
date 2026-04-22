"""FastAPI app — Twilio WhatsApp webhook + scheduled recap/wrap jobs.

Routes:

- ``GET  /health`` — liveness probe for Railway / uptime checks.
- ``POST /webhook`` — Twilio WhatsApp webhook. Accepts the form-encoded
  payload, dispatches to :func:`app.webhook.handle_inbound`, and returns
  a TwiML response that Twilio relays back to the sender (or a bare
  ``<Response/>`` when the handler chooses to stay silent).

Scheduled jobs (APScheduler, ``America/Los_Angeles``):

- **Daily recap / weekly wrap** — every day at 00:00 LA (the LinkedIn
  puzzle flip). Recaps the LA day that just closed. When that day is
  a Sunday, the emitted message is the full weekly wrap instead of a
  plain daily recap — see :func:`app.jobs.run_daily_recap`.

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

from typing import Callable, Optional

from .config import Settings, load_settings
from .db import InMemoryRepository, Repository, SupabaseRepository
from .puzzles import expected_puzzle_no as _expected_puzzle_no
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


def get_puzzle_validator() -> Optional[Callable[[str, datetime], int]]:
    """FastAPI dependency returning the puzzle-number validator.

    Production wires in :func:`app.puzzles.expected_puzzle_no`, which
    refuses any submission whose puzzle number isn't the one LinkedIn is
    serving today. Tests that want arbitrary puzzle numbers override
    this to return ``None`` via ``app.dependency_overrides``.
    """
    return _expected_puzzle_no


# ---------------------------------------------------------------------------
# APScheduler setup
# ---------------------------------------------------------------------------


def _setup_scheduler() -> None:
    """Create and start a BackgroundScheduler with the single daily job.

    One cron, fires at **00:00 America/Los_Angeles every day** — the
    LinkedIn puzzle rollover. :func:`app.jobs.run_daily_recap` decides
    which format to emit: a regular daily recap on Mon–Sat (LA), or
    the full weekly wrap when the closed day is a Sunday (which lands
    Monday afternoon Sydney time, just before the new puzzle drops).

    Runs inside the FastAPI lifespan so the scheduler starts after the
    app boots and shuts down when the app stops.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    from .jobs import run_daily_recap

    settings = load_settings()
    scheduler_tz = "America/Los_Angeles"

    scheduler = BackgroundScheduler()

    def _daily():
        run_daily_recap(get_repository(), settings)

    scheduler.add_job(
        _daily,
        CronTrigger(hour=0, minute=0, timezone=scheduler_tz),
        id="daily_recap",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started: daily_recap at 00:00 %s every day "
        "(Sunday fires the weekly wrap format)",
        scheduler_tz,
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


def _twiml(reply_text: Optional[str]) -> str:
    """Wrap ``reply_text`` in a minimal TwiML envelope.

    When ``reply_text`` is ``None`` we return a bare ``<Response/>`` —
    Twilio reads that as "no reply" and silently accepts the message,
    which is what we want for non-upload chatter in a group.
    """
    prolog = '<?xml version="1.0" encoding="UTF-8"?>'
    if reply_text is None:
        return f"{prolog}<Response/>"
    return f"{prolog}<Response><Message>{xml_escape(reply_text)}</Message></Response>"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


_ERROR_REPLY = (
    "Sorry, the bot hit an error processing your message. "
    "The admins have been notified — please try again in a bit."
)


@app.post("/webhook")
async def webhook(
    from_: str = Form(..., alias="From"),
    body: str = Form("", alias="Body"),
    profile_name: str = Form("", alias="ProfileName"),
    repo: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
    puzzle_validator: Optional[Callable[[str, datetime], int]] = Depends(
        get_puzzle_validator
    ),
) -> Response:
    # Any exception from handle_inbound (Supabase outage, misconfigured
    # tables, bad regex input, etc.) must NOT bubble up as a 500 — Twilio
    # can't relay a reply from a 500, so the sender sees silence and the
    # Twilio console shows a webhook error. Catch, log the full traceback
    # (visible in Railway logs), and return a valid TwiML apology.
    try:
        reply: Optional[str] = handle_inbound(
            repo,
            from_=from_,
            body=body,
            profile_name=profile_name,
            now=datetime.now(settings.tz),
            enabled_games=settings.enabled_games,
            expected_puzzle_no=puzzle_validator,
            settings=settings,
        )
    except Exception:
        logger.exception(
            "handle_inbound failed for from=%s body=%r",
            from_,
            (body or "")[:200],
        )
        reply = _ERROR_REPLY
    return Response(content=_twiml(reply), media_type="application/xml")


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request, exc):  # pragma: no cover
    # Second line of defense: if an exception escapes the handler above
    # (e.g. a Depends() dependency itself fails — bad Supabase client init,
    # unresolvable timezone), still return TwiML for /webhook instead of
    # a JSON 500 that Twilio can't display.
    logger.exception("Unhandled exception on %s", request.url.path)
    if request.url.path == "/webhook":
        return Response(
            content=_twiml(_ERROR_REPLY),
            media_type="application/xml",
            status_code=200,
        )
    return Response(
        content='{"detail":"Internal Server Error"}',
        media_type="application/json",
        status_code=500,
    )
