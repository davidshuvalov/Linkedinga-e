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


# Sentinel for ``send_recap``'s ``group_recap_to`` kwarg so we can tell
# "caller didn't pass anything" (legacy single-group callers, treat as
# settings.twilio_recap_to) from "caller explicitly passed None" (a
# per-group caller for a group with no group post target — skip the
# group post and DM-fan-out).
_UNSET = object()


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
    group_recap_to=_UNSET,
) -> None:
    """Post ``body`` to the group, falling back to per-player DMs.

    Args:
        settings: App settings (Twilio creds + from/to numbers).
        body: The formatted recap or wrap text.
        dm_targets: Optional list of ``whatsapp:+…`` IDs. Used as the
            fallback audience when group posting fails **and** as the
            primary audience when no group target is configured.
        group_recap_to: Per-group WhatsApp group post target. Three
            states:

            * ``_UNSET`` (default; legacy single-group callers) — use
              ``settings.twilio_recap_to`` if set, else DM-fan-out.
            * a string ``"whatsapp:+..."`` — post to that target,
              falling back to DMs on failure.
            * ``None`` — skip the group post entirely and DM-fan-out.
              Used by per-group cron jobs for groups that don't have
              their own group post target configured.
    """
    if not settings.twilio_account_sid:
        logger.warning(
            "TWILIO_ACCOUNT_SID not set — printing recap to stdout instead."
        )
        print(body)
        return

    # Resolve the actual group post target, distinguishing the three
    # caller states above.
    if group_recap_to is _UNSET:
        target_recap_to = settings.twilio_recap_to or None
    else:
        target_recap_to = group_recap_to  # may be None → skip group post
    if target_recap_to:
        if _send_one(settings, target_recap_to, body):
            return
        logger.warning(
            "Group post to %s failed; falling back to per-player DMs.",
            target_recap_to,
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


def send_dm(settings: Settings, to: str, body: str) -> bool:
    """Send a single direct WhatsApp message.

    Used by the morning-nudge job for per-player notifications. When
    Twilio credentials aren't configured (local / test), prints the
    message to stdout instead of calling the API so dev still works.
    """
    if not settings.twilio_account_sid:
        logger.warning(
            "TWILIO_ACCOUNT_SID not set — printing DM to stdout instead."
        )
        print(f"[DM to {to}]\n{body}\n")
        return True
    return _send_one(settings, to, body)
