"""Twilio WhatsApp message sender.

:func:`send_recap` is the single entry point called by scheduled jobs.
It tries to post to the group chat first (``TWILIO_RECAP_TO``). If group
messaging fails — which is a known Twilio WhatsApp limitation depending
on your account tier — it falls back to DMing each player individually.

In local / test environments where ``TWILIO_ACCOUNT_SID`` is not set the
sender logs the message to stdout instead of calling the Twilio API, so
the app can still boot and the scheduler can fire without crashing.
"""

from __future__ import annotations

import logging
from typing import List

from .config import Settings

logger = logging.getLogger(__name__)


def _get_twilio_client(settings: Settings):
    """Lazily import and construct the Twilio REST client."""
    from twilio.rest import Client  # type: ignore

    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def _send_one(settings: Settings, to: str, body: str) -> bool:
    """Send a single WhatsApp message. Returns True on success."""
    try:
        client = _get_twilio_client(settings)
        client.messages.create(
            from_=settings.twilio_whatsapp_from,
            to=to,
            body=body,
        )
        logger.info("Sent message to %s (%d chars)", to, len(body))
        return True
    except Exception:
        logger.exception("Failed to send message to %s", to)
        return False


def send_recap(
    settings: Settings,
    body: str,
    *,
    dm_targets: List[str] | None = None,
) -> None:
    """Post ``body`` to the group, falling back to per-player DMs.

    Args:
        settings: App settings (Twilio creds + from/to numbers).
        body: The formatted recap or wrap text.
        dm_targets: Optional list of ``whatsapp:+…`` IDs. Used as the
            fallback audience when group posting fails **and** as the
            primary audience when ``TWILIO_RECAP_TO`` is not configured.
    """
    if not settings.twilio_account_sid:
        logger.warning(
            "TWILIO_ACCOUNT_SID not set — printing recap to stdout instead."
        )
        print(body)
        return

    # Try group post first if a group target is configured.
    if settings.twilio_recap_to:
        if _send_one(settings, settings.twilio_recap_to, body):
            return
        logger.warning(
            "Group post to %s failed; falling back to per-player DMs.",
            settings.twilio_recap_to,
        )

    # Fallback: DM each player individually.
    if not dm_targets:
        logger.warning(
            "No dm_targets provided and group post unavailable; "
            "recap was not delivered."
        )
        return

    for target in dm_targets:
        _send_one(settings, target, body)
