"""Scheduled-job orchestration.

The single scheduled job is :func:`run_daily_recap`, wired to the
00:00 America/Los_Angeles cron in :func:`app.main._setup_scheduler`.
It fires at the LinkedIn puzzle rollover — the moment the previous
day's puzzles expire — and:

- On Mon–Sat (LA), emits the daily recap: per-game rankings for the
  day that just closed, plus a running "Week so far" leaderboard.
- On Sun (LA) — which in Sydney is Monday afternoon — emits the
  weekly wrap: Sunday's per-game rankings, final week totals, per-game
  weekly winners, and the three prizes.

Both formats are produced by :mod:`app.scheduler` (pure formatters).
This module just handles the I/O: figure out the date window, fetch
scores from the repository, call the formatter, and send via Twilio.

Same entrypoint also powers the on-demand ``recap`` and ``wrap``
commands the webhook exposes — see :func:`render_daily` /
:func:`render_wrap`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from .config import Settings
from .db import Repository
from .puzzles import la_date, week_bounds
from .scheduler import daily_recap, weekly_wrap
from .sender import send_recap

logger = logging.getLogger(__name__)


def _is_sunday(day: date) -> bool:
    return day.weekday() == 6


def render_daily(
    repo: Repository,
    settings: Settings,
    target_day: date,
) -> Tuple[str, List[str]]:
    """Build the daily-recap body + DM target list for ``target_day``.

    Returns ``(body, dm_targets)``. The body is either a daily recap
    (Mon–Sat LA) or a weekly wrap (Sun LA) — the logic lives here so
    both the scheduled job and the on-demand ``recap`` command pick up
    the same shape automatically.
    """
    monday, sunday = week_bounds(target_day)
    week_scores = repo.list_scores(date_from=monday, date_to=sunday)

    if _is_sunday(target_day):
        body = weekly_wrap(monday, sunday, week_scores,
                           enabled_games=settings.enabled_games)
    else:
        body = daily_recap(target_day, week_scores,
                           enabled_games=settings.enabled_games)

    dm_targets = repo.list_active_whatsapp_ids(
        date_from=monday, date_to=sunday
    )
    return body, dm_targets


def render_wrap(
    repo: Repository,
    settings: Settings,
    reference_day: date,
) -> Tuple[str, List[str]]:
    """Build the weekly-wrap body regardless of which day ``reference_day`` is.

    Used by the on-demand ``wrap`` command so a midweek user can pull
    the full wrap format (including partial per-game winners + prize
    snapshots) for the current in-progress week. The scheduled
    Monday-morning-LA job goes through :func:`render_daily` instead;
    that route auto-selects the weekly format when the closed day is
    a Sunday.
    """
    monday, sunday = week_bounds(reference_day)
    week_scores = repo.list_scores(date_from=monday, date_to=sunday)
    body = weekly_wrap(monday, sunday, week_scores,
                       enabled_games=settings.enabled_games)
    dm_targets = repo.list_active_whatsapp_ids(
        date_from=monday, date_to=sunday
    )
    return body, dm_targets


def run_daily_recap(
    repo: Repository,
    settings: Settings,
    *,
    now: Optional[datetime] = None,
) -> str:
    """Cron entry point — fire the daily recap (or weekly wrap on Sun).

    Called by the APScheduler job at 00:00 America/Los_Angeles. At that
    instant the new puzzle is dropping; the "target day" to recap is
    the LA day that just closed (``la_date(now) - 1``).
    """
    now = now or datetime.now(settings.tz)
    target_day = la_date(now) - timedelta(days=1)
    logger.info(
        "Running daily recap for LA day %s (sunday=%s)",
        target_day,
        _is_sunday(target_day),
    )

    body, dm_targets = render_daily(repo, settings, target_day)
    send_recap(settings, body, dm_targets=dm_targets)
    return body
