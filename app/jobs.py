"""Scheduled-job orchestration.

Each function is the top-level callable wired into APScheduler by
:func:`app.main._setup_scheduler`. Pattern: fetch from repo → format via
``app.scheduler`` → deliver via ``app.sender``.

Timing: both jobs fire at LA midnight (the LinkedIn puzzle rollover),
so "today" and "this week" are anchored in LA time. The daily job at
00:00 LA recaps the LA day that just closed (yesterday LA); the weekly
job at Mon 00:01 LA wraps the LA Mon–Sun week that just ended.

Dependencies are injected explicitly so the functions can be tested
without touching Twilio or Supabase.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .config import Settings
from .db import Repository
from .puzzles import la_date
from .scheduler import daily_recap, weekly_wrap
from .sender import send_recap

logger = logging.getLogger(__name__)


def run_daily_recap(
    repo: Repository,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> str:
    """Fetch scores for the LA day that just closed, format, and send.

    Called by the cron at 00:00 LA. At that moment the new puzzle is
    dropping; "today" in LA is the new day, so we recap **yesterday in
    LA** — which is the day whose puzzles just expired.

    Returns the formatted body (useful for testing / logging).
    """
    now = now or datetime.now(settings.tz)
    # Day that just closed = yesterday in LA time.
    closed_day = la_date(now) - timedelta(days=1)
    logger.info("Running daily recap for LA day %s", closed_day)

    scores = repo.list_scores(date_from=closed_day, date_to=closed_day)
    body = daily_recap(closed_day, scores, enabled_games=settings.enabled_games)

    dm_targets = repo.list_active_whatsapp_ids(
        date_from=closed_day, date_to=closed_day
    )
    send_recap(settings, body, dm_targets=dm_targets)
    return body


def run_weekly_wrap(
    repo: Repository,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> str:
    """Fetch scores for the LA Mon–Sun week that just ended, format, and send.

    Called by the cron at Monday 00:01 LA. The week we're closing out
    is the Mon–Sun whose Sunday just ended — i.e. the 7 LA days up to
    (and including) yesterday LA.

    Returns the formatted body (useful for testing / logging).
    """
    now = now or datetime.now(settings.tz)
    # Sunday LA that just ended.
    sunday = la_date(now) - timedelta(days=1)
    monday = sunday - timedelta(days=sunday.weekday())
    logger.info("Running weekly wrap for LA week %s – %s", monday, sunday)

    scores = repo.list_scores(date_from=monday, date_to=sunday)
    body = weekly_wrap(monday, sunday, scores, enabled_games=settings.enabled_games)

    dm_targets = repo.list_active_whatsapp_ids(
        date_from=monday, date_to=sunday
    )
    send_recap(settings, body, dm_targets=dm_targets)
    return body
