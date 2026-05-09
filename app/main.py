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
from xml.sax.saxutils import escape as xml_escape, quoteattr as xml_quoteattr

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
    """Create and start a BackgroundScheduler with the cron jobs.

    Two crons:

    - ``daily_recap`` — fires at **00:00 America/Los_Angeles every
      day** — the LinkedIn puzzle rollover.
      :func:`app.jobs.run_daily_recap` decides which format to emit:
      a regular daily recap on Mon–Sat (LA), or the full weekly wrap
      when the closed day is a Sunday. Skips if
      :func:`app.jobs.maybe_fire_early_recap` already covered the
      day (everyone played every game before the cron fired).
    - ``morning_nudge`` — fires at **08:30 Australia/Sydney every
      day**. DMs every recently-active opted-in player a list of
      games they haven't played yet today.

    Runs inside the FastAPI lifespan so the scheduler starts after
    the app boots and shuts down when the app stops.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    from .jobs import (
        PRE_RESET_STAGES,
        run_daily_recap,
        run_morning_nudge,
        run_new_games_announcement,
        run_pre_reset_warning,
        run_weekly_wrap_early,
    )

    settings = load_settings()
    recap_tz = "America/Los_Angeles"
    nudge_tz = "Australia/Sydney"

    scheduler = BackgroundScheduler()

    def _daily():
        run_daily_recap(get_repository(), settings)

    def _morning():
        run_morning_nudge(get_repository(), settings)

    def _new_games():
        run_new_games_announcement(get_repository(), settings)

    def _weekly_wrap():
        run_weekly_wrap_early(get_repository(), settings)

    # Each stage gets its own closure so APScheduler can hold a
    # distinct callable per cron job. A loop with late-binding
    # would have every job firing the last stage instead.
    def _make_warning(stage: str):
        def _warning():
            run_pre_reset_warning(get_repository(), settings, stage=stage)
        return _warning

    # Stage → minutes-before-LA-midnight. Drives the four escalating
    # nag crons; tone climbs with each one (see _PRE_RESET_TEMPLATES).
    pre_reset_schedule = {
        "2h":    (22, 0),
        "1h":    (23, 0),
        "30min": (23, 30),
        "5min":  (23, 55),
    }
    assert set(pre_reset_schedule) == set(PRE_RESET_STAGES)

    scheduler.add_job(
        _daily,
        CronTrigger(hour=0, minute=0, timezone=recap_tz),
        id="daily_recap",
        replace_existing=True,
    )
    scheduler.add_job(
        _morning,
        CronTrigger(hour=8, minute=30, timezone=nudge_tz),
        id="morning_nudge",
        replace_existing=True,
    )
    for stage, (hour, minute) in pre_reset_schedule.items():
        scheduler.add_job(
            _make_warning(stage),
            CronTrigger(hour=hour, minute=minute, timezone=recap_tz),
            id=f"pre_reset_warning_{stage}",
            replace_existing=True,
        )
    scheduler.add_job(
        _new_games,
        # One minute after the daily recap so the recap lands first
        # and this fires as the "and now go play" hype follow-up.
        CronTrigger(hour=0, minute=1, timezone=recap_tz),
        id="new_games_announcement",
        replace_existing=True,
    )
    scheduler.add_job(
        _weekly_wrap,
        # Sunday 23:59 LA — one minute BEFORE the new puzzle drop so
        # the wrap closes out the week before the next one starts.
        # = 16:59 Sydney Monday (PDT) / 18:59 Sydney Monday (PST).
        # The Mon 00:00 LA daily_recap cron above sees the wrap is
        # already marked sent and skips silently.
        CronTrigger(
            day_of_week="sun", hour=23, minute=59, timezone=recap_tz,
        ),
        id="weekly_wrap_early",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started: daily_recap at 00:00 %s, "
        "weekly_wrap_early at Sun 23:59 %s, "
        "new_games_announcement at 00:01 %s, "
        "morning_nudge at 08:30 %s, "
        "pre_reset_warning at %s %s",
        recap_tz,
        recap_tz,
        recap_tz,
        nudge_tz,
        ", ".join(
            f"{h:02d}:{m:02d} ({stage})"
            for stage, (h, m) in pre_reset_schedule.items()
        ),
        recap_tz,
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


# WhatsApp's per-message ceiling is 1600 chars; Twilio silently drops
# any TwiML <Message> body that exceeds it. Leave headroom for emoji
# rendering width and the occasional control char so we don't sit
# right at the cliff.
_MAX_MESSAGE_CHARS = 1500


def _chunk_message(body: str, max_chars: int = _MAX_MESSAGE_CHARS) -> list[str]:
    """Split ``body`` into <= ``max_chars`` chunks at section boundaries.

    Long recaps (e.g. the daily recap on the last day of the month, which
    appends Month totals + Game winners + Prizes) routinely break the
    1600-char WhatsApp ceiling. A single oversized ``<Message>`` is
    silently dropped by Twilio — the symptom is the bot going completely
    quiet for ``recap`` / ``today`` after a submit. Splitting at
    paragraph boundaries (``\\n\\n``) keeps each chunk under the limit
    while preserving section integrity; falling back to line splits
    handles the rare paragraph that's itself oversized.
    """
    if len(body) <= max_chars:
        return [body]

    chunks: list[str] = []
    current = ""
    for para in body.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        # Paragraph alone exceeds the ceiling — split on lines.
        if len(para) > max_chars:
            for line in para.split("\n"):
                line_candidate = f"{current}\n{line}" if current else line
                if len(line_candidate) <= max_chars:
                    current = line_candidate
                else:
                    if current:
                        chunks.append(current)
                    # A single line over the limit is unrealistic for
                    # our recap shape; hard-slice it as a last resort
                    # so we never silently drop content.
                    while len(line) > max_chars:
                        chunks.append(line[:max_chars])
                        line = line[max_chars:]
                    current = line
        else:
            current = para
    if current:
        chunks.append(current)
    return chunks


def _twiml(
    reply_text: Optional[str], status_callback_url: Optional[str] = None
) -> str:
    """Wrap ``reply_text`` in a minimal TwiML envelope.

    When ``reply_text`` is ``None`` we return a bare ``<Response/>`` —
    Twilio reads that as "no reply" and silently accepts the message,
    which is what we want for non-upload chatter in a group.

    Bodies over WhatsApp's 1600-char ceiling are split into multiple
    ``<Message>`` verbs so the recap actually reaches the user instead
    of being silently dropped by Twilio.

    When ``status_callback_url`` is set, every ``<Message>`` gets a
    ``statusCallback`` attribute pointing at it. Twilio POSTs the
    delivery status (queued / sent / delivered / failed / undelivered)
    of the outbound reply to that URL, which lets the bot log carrier
    rejections (e.g. WhatsApp 63012) instead of being completely blind
    to them — TwiML replies are otherwise fire-and-forget.
    """
    prolog = '<?xml version="1.0" encoding="UTF-8"?>'
    if reply_text is None:
        return f"{prolog}<Response/>"
    if status_callback_url:
        attr = f" statusCallback={xml_quoteattr(status_callback_url)}"
    else:
        attr = ""
    messages = "".join(
        f"<Message{attr}>{xml_escape(chunk)}</Message>"
        for chunk in _chunk_message(reply_text)
    )
    return f"{prolog}<Response>{messages}</Response>"


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
    return Response(
        content=_twiml(reply, settings.twilio_status_callback_url or None),
        media_type="application/xml",
    )


# Twilio statuses we treat as terminal failures worth logging at WARN.
# Everything else (queued, sending, sent, delivered, read, ...) is just
# normal lifecycle noise.
_FAILURE_STATUSES = frozenset({"failed", "undelivered"})


@app.post("/twilio-status")
async def twilio_status_callback(
    message_sid: str = Form("", alias="MessageSid"),
    message_status: str = Form("", alias="MessageStatus"),
    error_code: str = Form("", alias="ErrorCode"),
    error_message: str = Form("", alias="ErrorMessage"),
    to: str = Form("", alias="To"),
    from_: str = Form("", alias="From"),
) -> Response:
    """Receive Twilio outbound-message status updates.

    Wired up via the ``statusCallback`` attribute on every ``<Message>``
    verb in :func:`_twiml`. Twilio POSTs here for each lifecycle
    transition (queued → sent → delivered, or → failed / undelivered).
    We log only the terminal failures — those are the ones a human
    needs to see (typically WhatsApp 63012 "Channel provider returned
    an internal service error", which surfaces here even though the
    TwiML response itself returned 200).

    Always returns 200 with an empty body — Twilio retries on non-2xx
    and the only thing we'd be doing on retry is a duplicate log line.
    """
    if message_status in _FAILURE_STATUSES:
        logger.warning(
            "Twilio outbound failed: sid=%s status=%s error=%s msg=%r "
            "to=%s from=%s",
            message_sid,
            message_status,
            error_code or "(none)",
            error_message or "",
            to,
            from_,
        )
    return Response(status_code=200)


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
