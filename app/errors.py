"""In-process log of recent webhook failures, for diagnosing from WhatsApp.

When :func:`app.webhook.handle_inbound` raises, the sender gets a
generic apology ("the bot hit an error processing your message") and
the real traceback goes to ``logging`` — which on Railway means the
only way to find out *what* broke is to open the deploy logs. That is
a bad place to be when the person who runs the group is holding a
phone.

So every failure is also kept here: a small, bounded, thread-safe ring
buffer of the most recent ones, each with a short reference the sender
sees in the apology. Texting ``errors`` back to the bot renders them
(see :func:`format_recent`), so a failing message can be diagnosed from
the same chat it failed in.

Deliberately in-memory rather than a table:

- it must work when the failure *is* the database (a Supabase outage
  or a missing migration is the likeliest cause of a blanket failure,
  and a logger that writes to the broken store logs nothing);
- it needs no schema migration to start working on an existing deploy;
- the bot runs as a single process, so one buffer sees every request.

The trade-off is that the log is cleared by a restart or redeploy.
Railway logs remain the durable record; this is the fast path to
"what is it, right now".
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, List, Optional

# How many failures to keep. Small on purpose: the interesting ones are
# always the most recent, and the whole buffer has to fit in a WhatsApp
# message when rendered.
MAX_FAILURES = 10

# Longest inbound body we keep per entry — enough to recognise which
# message it was without storing an entire pasted share text.
_BODY_PREVIEW = 120


@dataclass(frozen=True)
class Failure:
    """One handler failure, as much as is useful to show a human."""

    ref: str
    when: datetime
    whatsapp_id: str
    body: str
    kind: str  # exception class name, or "Timeout"
    detail: str  # str(exc), or the timeout explanation
    location: str  # "file.py:123 in func" — innermost app frame
    traceback_text: str


_failures: Deque[Failure] = deque(maxlen=MAX_FAILURES)
_lock = threading.Lock()


def _innermost_app_frame(exc: BaseException) -> str:
    """Where in *our* code it blew up.

    The last frame of a traceback is often inside a library (httpx,
    postgrest, stdlib), which says nothing about which handler broke.
    Prefer the innermost frame belonging to this package and fall back
    to the true innermost frame when there isn't one.
    """
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "(no traceback)"
    ours = [f for f in frames if "/app/" in f.filename or "\\app\\" in f.filename]
    frame = (ours or frames)[-1]
    filename = frame.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return f"{filename}:{frame.lineno} in {frame.name}"


def _record(
    *,
    when: datetime,
    whatsapp_id: str,
    body: str,
    kind: str,
    detail: str,
    location: str,
    traceback_text: str,
) -> str:
    ref = uuid.uuid4().hex[:6]
    failure = Failure(
        ref=ref,
        when=when,
        whatsapp_id=whatsapp_id or "?",
        body=(body or "")[:_BODY_PREVIEW],
        kind=kind,
        detail=detail,
        location=location,
        traceback_text=traceback_text,
    )
    with _lock:
        _failures.append(failure)
    return ref


def record_exception(
    exc: BaseException, *, whatsapp_id: str, body: str, when: datetime
) -> str:
    """Log a handler exception. Returns its short reference."""
    return _record(
        when=when,
        whatsapp_id=whatsapp_id,
        body=body,
        kind=type(exc).__name__,
        detail=str(exc).strip() or "(no message)",
        location=_innermost_app_frame(exc),
        traceback_text="".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ),
    )


def record_timeout(
    *, whatsapp_id: str, body: str, when: datetime, seconds: float
) -> str:
    """Log a handler that overran its budget. Returns its reference.

    Not an exception — the handler is still running — but it's the same
    question for whoever is holding the phone ("why did it say that?"),
    so it lands in the same buffer.
    """
    return _record(
        when=when,
        whatsapp_id=whatsapp_id,
        body=body,
        kind="Timeout",
        detail=f"handler still running after {seconds:.0f}s",
        location="(no traceback — handler did not raise)",
        traceback_text="",
    )


def recent(limit: int = MAX_FAILURES) -> List[Failure]:
    """Most recent failures, newest first."""
    with _lock:
        entries = list(_failures)
    return list(reversed(entries))[:limit]


def find(ref: str) -> Optional[Failure]:
    """Look up one failure by the reference shown to the sender."""
    wanted = ref.strip().lower()
    with _lock:
        for failure in _failures:
            if failure.ref == wanted:
                return failure
    return None


def clear() -> None:
    """Drop every recorded failure (used by tests)."""
    with _lock:
        _failures.clear()


def format_recent(limit: int = 5) -> str:
    """Render the recent failures for a WhatsApp reply."""
    entries = recent(limit)
    if not entries:
        return (
            "No errors recorded since the last restart. "
            "(A redeploy clears this log — Railway logs keep the full history.)"
        )

    lines = [f"Recent bot errors ({len(entries)}):"]
    for failure in entries:
        lines.append("")
        lines.append(
            f"  {failure.ref} · {failure.when:%a %d %b %H:%M} · {failure.whatsapp_id}"
        )
        lines.append(f"    msg: {failure.body!r}")
        lines.append(f"    {failure.kind}: {failure.detail}")
        lines.append(f"    at {failure.location}")
    lines.append("")
    lines.append("Send `errors <ref>` for the full traceback.")
    return "\n".join(lines)


def format_one(ref: str) -> str:
    """Render one failure's full traceback for a WhatsApp reply."""
    failure = find(ref)
    if failure is None:
        return (
            f"No error with reference {ref!r} in the log. "
            "Send `errors` to see what's there."
        )
    header = (
        f"Error {failure.ref} · {failure.when:%a %d %b %H:%M}\n"
        f"From: {failure.whatsapp_id}\n"
        f"Message: {failure.body!r}\n"
        f"{failure.kind}: {failure.detail}\n"
    )
    if not failure.traceback_text:
        return header + f"At: {failure.location}"
    return header + "\n" + failure.traceback_text.strip()
