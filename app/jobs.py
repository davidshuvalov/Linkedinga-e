"""Scheduled-job orchestration.

Two scheduled cron jobs:

- :func:`run_daily_recap` — fires at 00:00 America/Los_Angeles (the
  LinkedIn puzzle rollover). On Mon–Sat (LA) emits a daily recap;
  on Sun (LA) emits the full weekly wrap. Skips if the recap was
  already fired early via :func:`maybe_fire_early_recap`.
- :func:`run_morning_nudge` — fires at 08:30 Australia/Sydney.
  DMs each opted-in player who hasn't yet played all enabled games
  for today's LA puzzle day, listing what's still outstanding.

Plus an event-driven helper, :func:`maybe_fire_early_recap`, called
by the webhook after every successful score insert. When everyone
who's been active in the last 7 days has played all enabled games
for the day, it fires the daily recap (or weekly wrap on Sun LA)
immediately and marks it sent so the cron doesn't double-send.

Both formats are produced by :mod:`app.scheduler` (pure formatters).
This module handles the I/O: figure out the date window, fetch
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
from .parsers import GAME_DISPLAY, GAME_DISPLAY_ORDER
from .puzzles import (
    is_last_day_of_month,
    is_last_day_of_year,
    la_date,
    month_bounds,
    week_bounds,
    year_bounds,
)
from .scheduler import daily_recap, weekly_wrap
from .scoring import PlayerWeeklyStats, weekly_leaderboard
from .sender import send_dm, send_recap

logger = logging.getLogger(__name__)

# How far back a player can have last submitted to count as "active"
# (and therefore expected to play today). Seven days catches anyone
# in the natural weekly rhythm of LinkedIn games.
_ACTIVE_WINDOW_DAYS = 7


def _is_sunday(day: date) -> bool:
    return day.weekday() == 6


def _recap_type_for(day: date) -> str:
    """Daily recap on Mon–Sat (LA), weekly wrap on Sun (LA). Used as
    the ``recap_type`` key in ``recap_log`` so the early-fire path
    and the cron agree on whether a given day has been "covered"."""
    return "weekly" if _is_sunday(day) else "daily"


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

    # Month / year totals are only appended on the last day of the
    # respective period. One extra repo query each, gated behind the
    # date check so the common path stays cheap.
    month_scores = None
    if is_last_day_of_month(target_day):
        m_start, m_end = month_bounds(target_day)
        month_scores = repo.list_scores(date_from=m_start, date_to=m_end)

    year_scores = None
    if is_last_day_of_year(target_day):
        y_start, y_end = year_bounds(target_day)
        year_scores = repo.list_scores(date_from=y_start, date_to=y_end)

    if _is_sunday(target_day):
        body = weekly_wrap(
            monday, sunday, week_scores,
            enabled_games=settings.enabled_games,
            month_scores=month_scores,
            year_scores=year_scores,
        )
    else:
        body = daily_recap(
            target_day, week_scores,
            enabled_games=settings.enabled_games,
            month_scores=month_scores,
            year_scores=year_scores,
        )

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
) -> Optional[str]:
    """Cron entry point — fire the daily recap (or weekly wrap on Sun).

    Called by the APScheduler job at 00:00 America/Los_Angeles. At that
    instant the new puzzle is dropping; the "target day" to recap is
    the LA day that just closed (``la_date(now) - 1``).

    Skips silently if :func:`maybe_fire_early_recap` already sent this
    day's recap during the day — the recap_log entry is the source of
    truth for "has this been covered". Returns ``None`` in that case
    so callers can distinguish "fired" from "skipped (already sent)".
    """
    now = now or datetime.now(settings.tz)
    target_day = la_date(now) - timedelta(days=1)
    recap_type = _recap_type_for(target_day)

    if repo.has_recap_been_sent(target_day, recap_type):
        logger.info(
            "Skipping cron recap for LA day %s — already sent (%s)",
            target_day,
            recap_type,
        )
        return None

    logger.info(
        "Running daily recap for LA day %s (sunday=%s)",
        target_day,
        _is_sunday(target_day),
    )

    body, dm_targets = render_daily(repo, settings, target_day)
    send_recap(settings, body, dm_targets=dm_targets)
    repo.mark_recap_sent(target_day, recap_type)
    send_champion_loser_dms(repo, settings, target_day)
    return body


# ---------------------------------------------------------------------------
# Champion / wooden-spoon personal DMs (Sunday LA only)
# ---------------------------------------------------------------------------


def _fmt_weekly_points(val: float) -> str:
    """Cheap local copy of the points formatter — avoids pulling
    ``_pts`` across module boundaries."""
    if abs(val - round(val)) < 1e-9:
        p = int(round(val))
        return "1 pt" if p == 1 else f"{p} pts"
    return f"{val:.1f} pts"


_CHAMPION_TEMPLATES = (
    "MASSIVE congrats {name} — you are THIS WEEK'S CHAMPION with {points}. "
    "No time for losers, for you are THE champion. Flex responsibly.",
    "{name}. Week {iso}. CHAMPION. {points}. "
    "Somewhere, a loser is weeping. That loser is not you.",
    "Put the trophy on the mantelpiece, {name}. "
    "Week's over, you won it. {points}. King/queen behaviour.",
)
_LOSER_TEMPLATES = (
    "{name} — wooden spoon this week with {points}. "
    "Someone has to be the floor the champion dances on. "
    "New week, new chance. Probably.",
    "{name}, bad news: you finished last ({points}). "
    "Good news: only way is up. Medium news: we're all laughing.",
    "Congratulations {name}, you are this week's official {points} "
    "person of the week — aka last. Get your revenge Monday.",
)


def _pick_template(templates: tuple, key: int) -> str:
    return templates[key % len(templates)]


def send_champion_loser_dms(
    repo: Repository,
    settings: Settings,
    target_day: date,
) -> List[str]:
    """On Sunday LA only: DM the top and bottom of the week's final
    leaderboard with personalised congrats / roast copy.

    Silent on non-Sundays so callers can invoke it unconditionally
    after ``mark_recap_sent`` without branching on the day. Returns
    the ``whatsapp_id`` list that received a DM — exposed for tests
    and logging.
    """
    if not _is_sunday(target_day):
        return []
    if not settings.enabled_games:
        return []

    monday, sunday = week_bounds(target_day)
    week_scores = repo.list_scores(date_from=monday, date_to=sunday)
    filtered = [s for s in week_scores if s.game in settings.enabled_games]
    lb = weekly_leaderboard(filtered)
    # Need at least two players for champion vs. wooden-spoon to
    # mean anything — a solo player is neither a champion nor a loser.
    if len(lb) < 2:
        return []

    champion: PlayerWeeklyStats = lb[0]
    loser: PlayerWeeklyStats = lb[-1]

    players = repo.list_players_active_since(monday)
    by_id = {p.id: p for p in players}
    iso_week = target_day.isocalendar()[1]

    sent: List[str] = []
    pairs = (
        (champion, _CHAMPION_TEMPLATES),
        (loser, _LOSER_TEMPLATES),
    )
    for stats, templates in pairs:
        player = by_id.get(stats.player_id)
        if player is None or not player.notifications_enabled:
            continue
        # Key template choice on iso_week so the copy rotates
        # week-to-week but stays the same for both recipients in
        # one week (cleaner if they ever compare notes).
        body = _pick_template(templates, iso_week).format(
            name=player.display_name,
            points=_fmt_weekly_points(stats.total_points),
            iso=iso_week,
        )
        if send_dm(settings, player.whatsapp_id, body):
            sent.append(player.whatsapp_id)
    return sent


# ---------------------------------------------------------------------------
# Early-fire daily recap (event-driven, called from webhook)
# ---------------------------------------------------------------------------


def _everyone_done_today(
    repo: Repository, settings: Settings, today: date
) -> bool:
    """Has every recently-active player submitted every enabled game
    for ``today`` (LA)? Drives the early-fire decision."""
    if not settings.enabled_games:
        return False

    since = today - timedelta(days=_ACTIVE_WINDOW_DAYS)
    active_players = repo.list_players_active_since(since)
    if not active_players:
        return False

    today_scores = repo.list_scores(date_from=today, date_to=today)
    games_by_player: dict[int, set[str]] = {}
    for s in today_scores:
        if s.game in settings.enabled_games:
            games_by_player.setdefault(s.player_id, set()).add(s.game)

    enabled = set(settings.enabled_games)
    return all(
        games_by_player.get(p.id, set()) >= enabled
        for p in active_players
    )


def maybe_fire_early_recap(
    repo: Repository,
    settings: Settings,
    *,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """If everyone's done for today, send the recap now and mark it.

    Called from the webhook after each successful score insert. Cheap
    when nobody's done (one query for active players, one for today's
    scores). Returns the recap body if fired, ``None`` if not.
    """
    now = now or datetime.now(settings.tz)
    today = la_date(now)
    recap_type = _recap_type_for(today)

    if repo.has_recap_been_sent(today, recap_type):
        return None
    if not _everyone_done_today(repo, settings, today):
        return None

    logger.info(
        "Early-firing %s recap for LA day %s — everyone has played all games",
        recap_type,
        today,
    )
    body, dm_targets = render_daily(repo, settings, today)
    send_recap(settings, body, dm_targets=dm_targets)
    repo.mark_recap_sent(today, recap_type)
    send_champion_loser_dms(repo, settings, today)
    return body


# ---------------------------------------------------------------------------
# Morning nudge (cron)
# ---------------------------------------------------------------------------


def _build_morning_nudge(
    player_name: str,
    enabled_games: frozenset,
    played_games: set,
) -> str:
    """Render the per-player nudge body. ``played_games`` is the set of
    games the player has already submitted today; the message lists
    only the enabled games they still owe."""
    missing = [
        GAME_DISPLAY[g]
        for g in GAME_DISPLAY_ORDER
        if g in enabled_games and g not in played_games
    ]
    played = [
        GAME_DISPLAY[g]
        for g in GAME_DISPLAY_ORDER
        if g in enabled_games and g in played_games
    ]

    lines = [f"Morning {player_name}!"]
    if not played:
        lines.append("You haven't played any LinkedIn games today yet.")
    else:
        lines.append(f"Done so far: {', '.join(played)}.")
    lines.append("")
    lines.append("Still to play:")
    for game in missing:
        lines.append(f"  - {game}")
    lines.append("")
    lines.append("DM your shares back to me when you're done.")
    return "\n".join(lines)


def run_morning_nudge(
    repo: Repository,
    settings: Settings,
    *,
    now: Optional[datetime] = None,
) -> List[str]:
    """Cron entry point — DM each active player a list of games they
    haven't played today.

    Skips:
    - players with ``notifications_enabled = False``
    - players who've already played every enabled game today (no
      point nudging someone who's done)
    - days when no enabled games are configured

    Returns the list of whatsapp_ids that received a nudge — handy for
    tests and for logging the daily reach.
    """
    now = now or datetime.now(settings.tz)
    today = la_date(now)
    enabled = settings.enabled_games
    if not enabled:
        logger.info("Morning nudge: no enabled games configured, skipping")
        return []

    since = today - timedelta(days=_ACTIVE_WINDOW_DAYS)
    active_players = repo.list_players_active_since(since)
    if not active_players:
        logger.info("Morning nudge: no recently active players, skipping")
        return []

    today_scores = repo.list_scores(date_from=today, date_to=today)
    games_by_player: dict[int, set[str]] = {}
    for s in today_scores:
        if s.game in enabled:
            games_by_player.setdefault(s.player_id, set()).add(s.game)

    nudged: List[str] = []
    for player in active_players:
        if not player.notifications_enabled:
            continue
        played = games_by_player.get(player.id, set())
        if played >= set(enabled):
            continue  # they're already done — nothing to nudge about
        body = _build_morning_nudge(player.display_name, enabled, played)
        if send_dm(settings, player.whatsapp_id, body):
            nudged.append(player.whatsapp_id)

    logger.info(
        "Morning nudge sent to %d/%d active players (LA day %s)",
        len(nudged),
        len(active_players),
        today,
    )
    return nudged


# ---------------------------------------------------------------------------
# Pre-reset escalating warnings (cron)
# ---------------------------------------------------------------------------


# Valid escalation stages, in order. Used for cron registration in
# main.py and as the key into _PRE_RESET_TEMPLATES.
PRE_RESET_STAGES: Tuple[str, ...] = ("2h", "1h", "30min", "5min")


# Rotating templates per escalation stage. Tone climbs from a
# friendly heads-up at 2h to all-caps panic at 5m. Same day-ordinal
# rotation as the other nag jobs so every player sees the same
# flavour on the same night, but it shuffles night-to-night.
# ``{name}`` and ``{missing}`` are format fields (missing =
# comma-separated game list).
_PRE_RESET_TEMPLATES: dict = {
    "2h": (
        "Heads up {name} — 2 hours until LinkedIn flips the puzzles. "
        "Still owed: {missing}. Plenty of time. Probably.",
        "{name}, gentle nudge: 2 hours to reset. Outstanding: {missing}. "
        "No pressure. Yet.",
        "Two hours, {name}. {missing} still unplayed. "
        "You've done harder things before lunch.",
        "{name} — friendly ping. 2 hours until rollover. "
        "Owed: {missing}. Knock 'em out before dinner gets cold.",
    ),
    "1h": (
        "60 minutes, {name}. Outstanding: {missing}. "
        "The leaderboard is watching. So am I.",
        "{name} — one hour. {missing}. "
        "Time to stop pretending you'll get to it later.",
        "One hour to play {missing}, {name}. "
        "Or be the first name on tomorrow's wall of shame. Your call.",
        "{name}: 60 minutes left and still owe {missing}. "
        "Whatever you're doing right now, the puzzles are more important. "
        "Probably.",
    ),
    "30min": (
        "Thirty minutes, {name}. Still mocking the streak: {missing}. "
        "Get. In. There.",
        "{name}, what are you DOING. Half an hour. Outstanding: {missing}. "
        "The puzzles are RIGHT THERE.",
        "30 mins, {name}. {missing} unplayed. "
        "Embarrassing. For us. For you. For the bot.",
        "{name} — half hour warning. Owed: {missing}. "
        "Stop. Drop. Open LinkedIn. Solve. Repeat.",
    ),
    "5min": (
        "FIVE MINUTES {name}. {missing}. "
        "RUN. RUN NOW. WHY ARE YOU READING THIS. PLAY.",
        "{name}. 5. Minutes. {missing}. "
        "This is not a drill. This IS the drill. PLAY THEM.",
        "EMERGENCY {name}: 5 minutes, {missing} unplayed. "
        "Drop everything. Yes including dinner. "
        "Yes including the baby. (Don't drop the baby.) PLAY.",
        "{name} you absolute pillock — 5 minutes left. {missing}. "
        "If midnight catches you with these undone you forfeit "
        "all dignity, all rights, all my respect.",
        "{name}: 300 seconds. {missing}. "
        "GO GO GO GO GO. I will not be held responsible for "
        "what tomorrow's recap says about you.",
    ),
}


def _build_pre_reset_warning(
    player_name: str,
    missing_games: List[str],
    today: date,
    stage: str,
) -> str:
    """Render the per-stage nag body. Template choice rotates on the
    day's ordinal so the same player doesn't get the same zinger two
    nights in a row at the same stage."""
    templates = _PRE_RESET_TEMPLATES[stage]
    template = templates[today.toordinal() % len(templates)]
    return template.format(name=player_name, missing=", ".join(missing_games))


def run_pre_reset_warning(
    repo: Repository,
    settings: Settings,
    *,
    stage: str,
    now: Optional[datetime] = None,
) -> List[str]:
    """Cron entry point — DM each active player who hasn't completed
    every enabled game, with copy whose tone matches ``stage``.

    Wired up to four crons in :func:`app.main._setup_scheduler`,
    firing 2h / 1h / 30m / 5m before the LA midnight rollover. Each
    invocation re-checks who's still outstanding so a player who
    finishes between stages stops getting pinged.

    Same skip rules as :func:`run_morning_nudge`: opted-out players,
    players who've already finished, and days with no enabled games
    all bail silently. Returns the ``whatsapp_id`` list that received
    the nag — useful for tests and reach logging.
    """
    if stage not in _PRE_RESET_TEMPLATES:
        raise ValueError(f"unknown pre-reset stage: {stage!r}")

    now = now or datetime.now(settings.tz)
    today = la_date(now)
    enabled = settings.enabled_games
    if not enabled:
        logger.info(
            "Pre-reset warning [%s]: no enabled games configured, skipping",
            stage,
        )
        return []

    since = today - timedelta(days=_ACTIVE_WINDOW_DAYS)
    active_players = repo.list_players_active_since(since)
    if not active_players:
        logger.info(
            "Pre-reset warning [%s]: no recently active players, skipping",
            stage,
        )
        return []

    today_scores = repo.list_scores(date_from=today, date_to=today)
    games_by_player: dict[int, set[str]] = {}
    for s in today_scores:
        if s.game in enabled:
            games_by_player.setdefault(s.player_id, set()).add(s.game)

    warned: List[str] = []
    for player in active_players:
        if not player.notifications_enabled:
            continue
        played = games_by_player.get(player.id, set())
        missing_ids = [
            g for g in GAME_DISPLAY_ORDER if g in enabled and g not in played
        ]
        if not missing_ids:
            continue  # player is done — no warning needed
        missing_labels = [GAME_DISPLAY[g] for g in missing_ids]
        body = _build_pre_reset_warning(
            player.display_name, missing_labels, today, stage
        )
        if send_dm(settings, player.whatsapp_id, body):
            warned.append(player.whatsapp_id)

    logger.info(
        "Pre-reset warning [%s] sent to %d/%d active players (LA day %s)",
        stage,
        len(warned),
        len(active_players),
        today,
    )
    return warned


# ---------------------------------------------------------------------------
# New-games announcement (cron, 00:01 LA)
# ---------------------------------------------------------------------------


# Group-blast copy that fires the moment new puzzles are live. No
# per-player check — purely a "go play" hype message. Ordinal rotation
# keeps it from going stale.
_NEW_GAMES_TEMPLATES: Tuple[str, ...] = (
    "New games are LIVE. Today's LinkedIn puzzles just dropped. "
    "Get in it. First share posted gets eternal glory (and zero prizes).",
    "Fresh puzzles, fresh chance to embarrass everyone. "
    "Today's games are out — get in it.",
    "The board is reset. Yesterday is forgotten. New puzzles are LIVE. "
    "First DM with a Queens share is the leader. Get in it.",
    "New games available. Stop scrolling. Open LinkedIn. Crush them. "
    "Get in it.",
    "Day flipped. Puzzles flipped. Leaderboard flipped. "
    "New games are out — get in it before someone else does.",
)


def run_new_games_announcement(
    repo: Repository,
    settings: Settings,
    *,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Cron entry point — fire the "new games are live, get in it"
    blast right after the daily rollover.

    Group post via :func:`send_recap` (same fan-out used by the daily
    recap), with the recently-active roster as the DM fallback so
    nobody's missed when the group post fails. Bails silently when
    no one's been active in the last week.

    Returns the body that was sent, or ``None`` if no audience.
    """
    now = now or datetime.now(settings.tz)
    today = la_date(now)

    body = _NEW_GAMES_TEMPLATES[
        today.toordinal() % len(_NEW_GAMES_TEMPLATES)
    ]

    since = today - timedelta(days=_ACTIVE_WINDOW_DAYS)
    dm_targets = repo.list_active_whatsapp_ids(date_from=since, date_to=today)
    if not dm_targets:
        logger.info(
            "New games announcement: no active players, skipping (LA day %s)",
            today,
        )
        return None

    send_recap(settings, body, dm_targets=dm_targets)
    logger.info("New games announcement sent (LA day %s)", today)
    return body
