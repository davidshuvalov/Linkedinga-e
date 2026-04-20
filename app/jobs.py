"""Scheduled-job orchestration.

Each function is the top-level callable wired into APScheduler by
:func:`app.main.setup_scheduler`. Pattern: fetch from repo → format via
``app.scheduler`` → deliver via ``app.sender``.

Dependencies are injected explicitly so the functions can be tested
without touching Twilio or Supabase.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .config import Settings
from .db import Repository
from .scheduler import daily_recap, weekly_wrap
from .sender import send_recap

logger = logging.getLogger(__name__)


def run_daily_recap(
    repo: Repository,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> str:
    """Fetch today's scores, format the recap, and send it.

    Returns the formatted body (useful for testing / logging).
    """
    now = now or datetime.now(settings.tz)
    today = now.date()
    logger.info("Running daily recap for %s", today)

    scores = repo.list_scores(date_from=today, date_to=today)
    body = daily_recap(today, scores)

    dm_targets = repo.list_active_whatsapp_ids(
        date_from=today, date_to=today
    )
    send_recap(settings, body, dm_targets=dm_targets)
    return body


def run_weekly_wrap(
    repo: Repository,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> str:
    """Fetch this week's scores, format the wrap, and send it.

    Returns the formatted body (useful for testing / logging).
    """
    now = now or datetime.now(settings.tz)
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    logger.info("Running weekly wrap for %s – %s", monday, sunday)

    scores = repo.list_scores(date_from=monday, date_to=sunday)
    body = weekly_wrap(monday, sunday, scores)

    dm_targets = repo.list_active_whatsapp_ids(
        date_from=monday, date_to=sunday
    )
    send_recap(settings, body, dm_targets=dm_targets)
    return body
