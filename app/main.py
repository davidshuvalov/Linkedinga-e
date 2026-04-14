"""FastAPI app — Twilio WhatsApp webhook entrypoint.

Routes:

- ``GET  /health`` — liveness probe for Railway / uptime checks.
- ``POST /webhook`` — Twilio WhatsApp webhook. Accepts the form-encoded
  payload, dispatches to :func:`app.webhook.handle_inbound`, and returns
  a TwiML response that Twilio relays back to the sender.

Run locally::

    uvicorn app.main:app --reload

With no Supabase credentials configured the app falls back to an
in-memory repository so you can smoke-test the webhook end-to-end.
"""

from __future__ import annotations

from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

from fastapi import Depends, FastAPI, Form, Response

from .config import Settings, load_settings
from .db import InMemoryRepository, Repository, SupabaseRepository
from .webhook import handle_inbound

app = FastAPI(title="LinkedIn Games WhatsApp Score Tracker")


# Lazy singleton so we don't construct the Supabase client at import time.
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
        # Imported lazily so tests don't need the supabase package.
        from supabase import create_client  # type: ignore

        client = create_client(settings.supabase_url, settings.supabase_key)
        _repo_singleton = SupabaseRepository(client)
    else:
        _repo_singleton = InMemoryRepository()
    return _repo_singleton


def get_settings() -> Settings:
    """FastAPI dependency returning the current :class:`Settings`."""
    return load_settings()


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
