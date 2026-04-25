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
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import Settings
from .db import Player, Repository, ScoreRow
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
    *,
    include_missing_today_nag: bool = True,
) -> Tuple[str, List[str]]:
    """Build the daily-recap body + DM target list for ``target_day``.

    Returns ``(body, dm_targets)``. The body is either a daily recap
    (Mon–Sat LA) or a weekly wrap (Sun LA) — the logic lives here so
    both the scheduled job and the on-demand ``recap`` command pick up
    the same shape automatically.

    ``include_missing_today_nag`` controls the passive-aggressive
    "Haven't heard from X today" footer. The scheduled cron leaves
    this on (the nag is the whole point of an active-day recap);
    on-demand recaps for *past* days drop it because the nag doesn't
    apply retroactively.
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
            include_missing_today_nag=include_missing_today_nag,
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


def run_weekly_wrap_early(
    repo: Repository,
    settings: Settings,
    *,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Cron entry point — fire the Sunday weekly wrap at 23:59 LA,
    one minute before the new puzzle drop.

    The original Mon 00:00 LA recap cron *also* fires the wrap when
    closing Sunday, but firing it 1 minute earlier (still on the LA
    Sunday) lands it before LinkedIn rolls — the wrap arrives in
    Sydney at 16:59 Mon, and the new-games message follows at 17:01,
    so the closing-out and the kicking-off don't collide.

    Idempotent via ``recap_log`` (uses the same ``"weekly"`` key the
    Mon 00:00 cron checks). Bails silently if it's not actually
    Sunday LA — guards against accidental triggering.
    """
    now = now or datetime.now(settings.tz)
    target_day = la_date(now)
    if not _is_sunday(target_day):
        logger.info(
            "weekly_wrap_early called on a non-Sunday LA day (%s) — skipping",
            target_day,
        )
        return None
    if repo.has_recap_been_sent(target_day, "weekly"):
        logger.info(
            "Skipping early weekly wrap for LA Sun %s — already sent",
            target_day,
        )
        return None
    logger.info("Running early weekly wrap for LA Sun %s", target_day)

    body, dm_targets = render_daily(repo, settings, target_day)
    send_recap(settings, body, dm_targets=dm_targets)
    repo.mark_recap_sent(target_day, "weekly")
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
    "Hear ye hear ye — {name} has TAKEN week {iso}. {points}. "
    "All hail. Brief reign. Lap it up.",
    "{name} you absolute MENACE. Week {iso} champion with {points}. "
    "Print it. Frame it. Mention it at parties.",
    "Week {iso}: solved. By {name}. {points}. "
    "Try not to peak too early. (Joke. You already have.)",
    "Trophy room update, {name}: one new entry. {points}. "
    "Champion of week {iso}. Insufferable until Sunday next.",
    "{name}, the leaderboard called. It said your name. {points}. "
    "Champion. No notes.",
    "Crown's yours this week, {name}. {points}. "
    "Wear it like you mean it.",
    "{name} took week {iso} like it was personal. {points}. "
    "Maybe it was. Who knows. Champion regardless.",
    "{name}: week {iso}, {points}, undisputed. "
    "We came, we saw, you conquered. Mostly the last one.",
    "Big news, {name}: you won. The whole week. {points}. "
    "Everybody else? Spectators. You? CHAMPION.",
    "{name} — week {iso} closes with you on top. {points}. "
    "Bask. You earned it. Briefly.",
    "Officially the best of week {iso}: {name}. {points}. "
    "Print this DM. Frame this DM. Show the children.",
    "{name}, look at you. CHAMPION of week {iso} with {points}. "
    "Don't peak now. Or do. Either's fine.",
)
_LOSER_TEMPLATES = (
    "{name} — wooden spoon this week with {points}. "
    "Someone has to be the floor the champion dances on. "
    "New week, new chance. Probably.",
    "{name}, bad news: you finished last ({points}). "
    "Good news: only way is up. Medium news: we're all laughing.",
    "Congratulations {name}, you are this week's official {points} "
    "person of the week — aka last. Get your revenge Monday.",
    "{name}, the leaderboard called. It put you on hold. {points}. "
    "Last place. We've all been there. Some of us only briefly.",
    "{name} — the floor is cosy down there, isn't it. {points}. "
    "Last this week. Mondays exist for a reason.",
    "{name} you finished week {iso} on a mighty {points}. "
    "Last. But you SHOWED UP, which is honestly more than some.",
    "Wooden spoon recipient, week {iso}: {name}. {points}. "
    "Comes with a small ceremony. Mostly pity claps.",
    "{name}, somebody had to be last. The universe picked you. "
    "{points}. Don't take it personally. Take it next week.",
    "{name} — week {iso} in the rear-view, {points}, last place. "
    "Tactical retreat. Regroup. Avenge.",
    "{name}, you ended week {iso} with {points}. "
    "On the bright side: you can only improve. On the other bright side: easily.",
    "Drum roll for last place, week {iso}: {name}, {points}. "
    "We did the drum roll. It was sad. Get 'em next week.",
    "{name} — wooden spoon awarded with {points}. "
    "Comes engraved with the words \"better luck Monday.\"",
    "{name} brought up the rear of week {iso} with {points}. "
    "Somebody's gotta hold the floor up. Today: you.",
    "{name}, the leaderboard is shaped a bit like a hill, and you are very much the bottom of it. "
    "{points}. Climb back up.",
    "{name} closed week {iso} on {points}. Last. "
    "Tomorrow's a new week. Tomorrow's also Monday. Brace.",
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


# Rotating greeting + sign-off pools so the morning nudge doesn't read
# the same to the same player every day. ``today.toordinal()`` picks
# the index, matching the rotation strategy used by the other nag
# jobs. Keep ``"Still to play:"`` as a literal section header — tests
# split on it.
_MORNING_OPENERS_NO_PROGRESS = (
    "Morning {name}! You haven't played any LinkedIn games today yet.",
    "Top of the morning, {name}. Zero puzzles solved. Plenty of day left.",
    "{name} — fresh day, blank scorecard. Time to fix that.",
    "Morning {name}. Reporting in: nothing played, nothing won.",
    "{name}, the puzzles are warm. The keyboard is cold. Coincidence?",
    "Heads up, {name}: today's puzzles are dropping silently into oblivion. Want to save them?",
    "{name}, the leaderboard misses you already. Today is salvageable.",
    "Morning {name} — the games are out there, mocking your absence.",
    "{name}, day's on. Puzzles untouched. Reputation: pending.",
    "Rise and grind, {name}. The puzzles aren't grinding themselves.",
    "Morning {name}! The puzzles are sitting there, untouched, judging.",
    "{name}, gentle reminder: the day is happening WITH or without you. With is better.",
    "Hello {name}. The leaderboard has a {name}-shaped gap in it. Fill it.",
    "Bright and early, {name}. Bright and unplayed.",
    "{name} — the early-bird recap window is open. Be the early bird.",
    "Morning {name}. Five tiny puzzles stand between you and bragging rights.",
    "{name}, today's slate: blank. Today's potential: high. Today's actual: tbd.",
    "Up and at 'em, {name}. The puzzles are at 'em already.",
)

_MORNING_OPENERS_PARTIAL = (
    "Morning {name}! Done so far: {played}.",
    "Off to a flyer, {name}. Knocked over: {played}.",
    "{name}, nicely warmed up. Already in the bag: {played}.",
    "Decent start, {name}. Banked: {played}.",
    "Morning {name} — got you down for: {played}. Tidy.",
    "{name}, ticking the boxes. Played: {played}.",
    "Solid, {name}. You've already crushed: {played}.",
    "Morning {name}. On the board with: {played}.",
    "{name} — the early bird, etc. So far: {played}.",
    "{name}, look at you go. Done: {played}.",
    "Cracking start, {name}. Bagged: {played}.",
    "{name}, momentum spotted. Cleared: {played}.",
    "Morning {name}. Already in the books: {played}. Don't stop now.",
    "{name} — partial credit on the board: {played}. Finish the homework.",
    "Halfway clever, {name}. Finished: {played}.",
    "{name}, you didn't even need the nudge for: {played}. Showoff.",
    "Morning {name} — {played} done, leaderboard already noticing.",
)

_MORNING_SIGN_OFFS = (
    "DM your shares back to me when you're done.",
    "Send your shares my way once you've cracked them.",
    "Forward me each share when you finish — I'll handle the rest.",
    "Smash 'em out and DM me the shares.",
    "Shares to me when complete. Easy.",
    "Drop the shares in here as you go.",
    "DM me your shares when each one's done — that's how you make it onto the board.",
    "Each share -> me. Standard procedure.",
    "Forward shares my way as you finish. I'll do the maths.",
    "Done a puzzle? Share it here. Repeat as needed.",
    "Shares incoming -> here. The leaderboard will reward your effort. Modestly.",
    "Send shares this way as you go. Don't make me chase them down.",
    "Share each one back here. Quietly. Loudly. Smugly. Up to you.",
)


# Contextual riff pools — one of these can prepend the rotating
# opener when a stat about the recipient is interesting enough to
# call out. Triggers gathered per-player; if multiple match, one is
# picked at random (no priority order). Falls back to the plain
# opener when nothing fires.
_NUDGE_CTX_LEADER_TEMPLATES: Tuple[str, ...] = (
    "{name}, you're leading the weekly leaderboard ({points}). Insufferable allowed.",
    "Top of the table, {name} — {points}. Don't blow it.",
    "Currently the player to beat, {name} ({points}). Press the advantage.",
    "{name}, week's leaderboard sits at: 1. you, {points}. Lap of honour optional.",
    "You're #1 this week, {name} ({points}). The view from the top.",
    "{name} is the current week leader at {points}. Defend the throne.",
    "Pole position, {name}: {points} this week. Stay there.",
    "{name}, leaderboard says you. {points}. Try to look surprised.",
    "Crown's still yours, {name} — {points} on the week. Don't drop it.",
    "{name} sits atop the weekly board with {points}. Earn it again today.",
)
_NUDGE_CTX_LAST_DAY_TEMPLATES: Tuple[str, ...] = (
    # ``{period}`` interpolates "week" / "month" / "year".
    "Heads up {name} — last day of the {period}. Standings lock at midnight.",
    "{name}, today closes the {period}. Make it count.",
    "Last day of the {period}, {name}. No second chances.",
    "{name} — final day of the {period}. Stand and deliver.",
    "Today's the {period}'s last hurrah, {name}. Go big or be remembered for going small.",
    "{name}, the {period} ends tonight. Whatever you do today is the last word.",
    "Closing day of the {period}, {name}. The tape is in sight.",
    "{name}, the {period} doesn't get rewritten after midnight. Your move.",
    "Final-of-the-{period} energy required, {name}. The bot will be watching.",
    "{name} — last day of the {period}. Tomorrow's standings are decided today.",
)
_NUDGE_CTX_STREAK_TEMPLATES: Tuple[str, ...] = (
    # ``{game}`` and ``{streak}`` (an int) get interpolated.
    "{name}, you've won {game} {streak} days running. Don't blow it now.",
    "{streak}-day {game} win streak going for {name}. Today extends it. Or kills it.",
    "Hot streak alert, {name}: you've taken {game} {streak} days in a row.",
    "{name} has won {game} {streak} times running. The pressure mounts.",
    "{streak} consecutive {game} wins, {name}. Today's a chance to make it {streak_plus_one}.",
    "{name}, the {game} crown's been yours for {streak} days. Hold the line.",
    "Quietly running away with it — {name} has won {game} {streak} days straight.",
    "{name}, your {game} streak sits at {streak}. Today's the test.",
    "{streak} on the trot, {name} — {game} crown still yours. Don't choke.",
    "{name} has owned {game} for {streak} days. Reign continues today, or doesn't.",
)


@dataclass
class _NudgeContext:
    """One contextual riff matching the recipient. ``kind`` keys into
    a template pool; ``format_data`` carries the fields the templates
    interpolate."""

    kind: str
    format_data: Dict[str, Any]


def _gather_nudge_context_triggers(
    *,
    player: Player,
    today: date,
    week_scores: Sequence[ScoreRow],
    recent_scores: Sequence[ScoreRow],
    enabled_games: frozenset,
) -> List[_NudgeContext]:
    """Return every contextual nudge trigger that fires for ``player``
    on ``today``. Caller picks one at random.

    ``week_scores`` is the current week's scores filtered to enabled
    games (used for the leader detector). ``recent_scores`` is the
    last ~14 days of scores (used for the streak detector — needs
    enough history to count back).
    """
    triggers: List[_NudgeContext] = []

    leader = _detect_leader_trigger(player, week_scores)
    if leader is not None:
        triggers.append(leader)

    last_day = _detect_last_day_trigger(today)
    if last_day is not None:
        triggers.append(last_day)

    streak = _detect_streak_trigger(
        player=player,
        today=today,
        recent_scores=recent_scores,
        enabled_games=enabled_games,
    )
    if streak is not None:
        triggers.append(streak)

    return triggers


def _detect_leader_trigger(
    player: Player, week_scores: Sequence[ScoreRow]
) -> Optional[_NudgeContext]:
    """Fire when ``player`` is currently top of the weekly leaderboard
    AND the field has at least 2 players (no point taunting yourself
    when you're alone on the board)."""
    lb = weekly_leaderboard(list(week_scores))
    if len(lb) < 2:
        return None
    leader = lb[0]
    if leader.player_id != player.id:
        return None
    return _NudgeContext(
        kind="leader",
        format_data={"_points_raw": str(leader.total_points)},
    )


def _detect_last_day_trigger(today: date) -> Optional[_NudgeContext]:
    """Fire on the last day of the year, then month, then week (in
    that order of "biggest period". A single trigger fires per call —
    we pick the largest period that closes today so the nudge
    headlines it correctly."""
    if is_last_day_of_year(today):
        return _NudgeContext(kind="last_day", format_data={"period": "year"})
    if is_last_day_of_month(today):
        return _NudgeContext(kind="last_day", format_data={"period": "month"})
    # Sunday LA closes the week — Python's weekday() has Monday=0,
    # Sunday=6. Match that with our LA-anchored convention.
    if today.weekday() == 6:
        return _NudgeContext(kind="last_day", format_data={"period": "week"})
    return None


def _detect_streak_trigger(
    *,
    player: Player,
    today: date,
    recent_scores: Sequence[ScoreRow],
    enabled_games: frozenset,
) -> Optional[_NudgeContext]:
    """Fire when ``player`` has won a single game on N consecutive
    days ending yesterday. "Won" means lowest raw_score on that
    day's submission for that game across all players who played.
    Threshold: 2+ days (a one-day "streak" isn't a streak).

    If multiple games qualify, picks the one with the longest
    streak. Ties broken by display order in
    :data:`GAME_DISPLAY_ORDER`.
    """
    # Group recent scores by (game, puzzle_date).
    by_game_day: Dict[Tuple[str, date], List[ScoreRow]] = {}
    for s in recent_scores:
        if s.game not in enabled_games:
            continue
        if s.puzzle_date >= today:
            continue  # only count past days; today's still in progress
        by_game_day.setdefault((s.game, s.puzzle_date), []).append(s)

    best_game: Optional[str] = None
    best_streak = 0
    for game in GAME_DISPLAY_ORDER:
        if game not in enabled_games:
            continue
        # Walk back day by day from yesterday, counting consecutive
        # days where ``player`` was the day's winner.
        streak = 0
        cursor = today - timedelta(days=1)
        while True:
            day_scores = by_game_day.get((game, cursor))
            if not day_scores:
                break  # game wasn't played that day → streak ends
            day_winner = min(day_scores, key=lambda s: s.raw_score)
            if day_winner.player_id != player.id:
                break
            streak += 1
            cursor -= timedelta(days=1)
        if streak > best_streak:
            best_streak = streak
            best_game = game

    if best_game is None or best_streak < 2:
        return None
    return _NudgeContext(
        kind="streak",
        format_data={
            "game": GAME_DISPLAY[best_game],
            "streak": str(best_streak),
            "streak_plus_one": str(best_streak + 1),
        },
    )


def _render_nudge_context(context: _NudgeContext, *, player_name: str) -> str:
    """Pick a random template from the matching pool and resolve
    placeholders. Returns one rendered line (no trailing newline)."""
    pool: Tuple[str, ...]
    if context.kind == "leader":
        pool = _NUDGE_CTX_LEADER_TEMPLATES
    elif context.kind == "last_day":
        pool = _NUDGE_CTX_LAST_DAY_TEMPLATES
    elif context.kind == "streak":
        pool = _NUDGE_CTX_STREAK_TEMPLATES
    else:
        raise ValueError(f"unknown nudge context kind: {context.kind!r}")

    template = random.choice(pool)
    fd = context.format_data
    subs: Dict[str, Any] = {"name": player_name}
    if "_points_raw" in fd:
        subs["points"] = _fmt_weekly_points(float(fd["_points_raw"]))
    for key in ("period", "game", "streak", "streak_plus_one"):
        if key in fd:
            subs[key] = fd[key]
    return template.format(**subs)


def _build_morning_nudge(
    player_name: str,
    enabled_games: frozenset,
    played_games: set,
    *,
    today: Optional[date] = None,
    context_line: Optional[str] = None,
) -> str:
    """Render the per-player nudge body. ``played_games`` is the set of
    games the player has already submitted today; the message lists
    only the enabled games they still owe.

    ``today`` drives the day-ordinal rotation across the opener and
    sign-off pools so the same player doesn't read identical copy
    every morning. Falls back to ``date.today()`` for callers that
    don't pass it (tests, ad-hoc invocations).

    ``context_line``, when supplied, prepends a one-line riff before
    the rotating opener — used to call out a leaderboard position,
    last-day-of-period notice, or active win-streak. Caller decides
    when to populate it; the renderer just slots it in."""
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

    ordinal = (today or date.today()).toordinal()

    if not played:
        opener = _MORNING_OPENERS_NO_PROGRESS[
            ordinal % len(_MORNING_OPENERS_NO_PROGRESS)
        ].format(name=player_name)
    else:
        opener = _MORNING_OPENERS_PARTIAL[
            ordinal % len(_MORNING_OPENERS_PARTIAL)
        ].format(name=player_name, played=", ".join(played))

    sign_off = _MORNING_SIGN_OFFS[ordinal % len(_MORNING_SIGN_OFFS)]

    lines: List[str] = []
    if context_line:
        lines.append(context_line)
        lines.append("")
    lines.append(opener)
    lines.append("")
    lines.append("Still to play:")
    for game in missing:
        lines.append(f"  - {game}")
    lines.append("")
    lines.append(sign_off)
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

    # Pull two date ranges once, reuse per player for the contextual
    # triggers below. ``week_scores`` drives the leader detector;
    # ``recent_scores`` (last 14 days incl. today) drives the
    # streak detector. Both are filtered to enabled games.
    monday, sunday = week_bounds(today)
    week_scores = [
        s for s in repo.list_scores(date_from=monday, date_to=sunday)
        if s.game in enabled
    ]
    recent_scores = [
        s for s in repo.list_scores(
            date_from=today - timedelta(days=14), date_to=today
        )
        if s.game in enabled
    ]

    nudged: List[str] = []
    for player in active_players:
        if not player.notifications_enabled:
            continue
        played = games_by_player.get(player.id, set())
        if played >= set(enabled):
            continue  # they're already done — nothing to nudge about

        context_line: Optional[str] = None
        try:
            triggers = _gather_nudge_context_triggers(
                player=player,
                today=today,
                week_scores=week_scores,
                recent_scores=recent_scores,
                enabled_games=enabled,
            )
            if triggers:
                chosen = random.choice(triggers)
                context_line = _render_nudge_context(
                    chosen, player_name=player.display_name
                )
        except Exception:
            logger.exception(
                "Morning nudge context-trigger gathering failed for player %s",
                player.id,
            )

        body = _build_morning_nudge(
            player.display_name, enabled, played,
            today=today,
            context_line=context_line,
        )
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
        "{name}: 120 minutes on the clock. Outstanding: {missing}. "
        "Future-you will thank past-you. Maybe.",
        "Two hours and counting, {name}. {missing} hasn't played itself. "
        "It's not going to.",
        "Polite reminder, {name} — 2h to puzzle rollover. {missing} pending. "
        "I'm not your mother. But I am, briefly, your mother.",
        "{name}, the rollover bus leaves in 2 hours. Boarding: {missing}. "
        "Don't make me chase you down the platform.",
        "Friendly heads-up, {name}: 2 hours, {missing} outstanding. "
        "You've still got time to look smart on the leaderboard.",
        "{name} — 2h until midnight (LA). {missing} on the to-do list. "
        "Lots of runway. Fly the plane.",
        "{name}, 2 hours and {missing} unplayed. "
        "Plenty of time to do it. Plenty of time to procrastinate. Choose.",
        "T-minus 120 minutes, {name}. {missing} on standby. "
        "Tap the puzzle. Solve the puzzle. Repeat.",
        "{name} — early warning system: 2h. {missing} pending. "
        "All systems go. Pilot is you. Plane is the puzzles. Move.",
        "{name}, 2-hour heads-up. {missing}. "
        "You can do this in 15 minutes flat. The other 1h45 is yours.",
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
        "{name}, t-minus 60 minutes. {missing} unplayed. "
        "I'd ask if you're ok but I already know the answer.",
        "One hour, {name}. {missing}. "
        "The window is closing. The puzzles are not. Yet.",
        "{name} — 60 to go, {missing} on the slate. "
        "You said \"in a sec\" four hours ago. The sec is now.",
        "Single hour left, {name}. {missing}. "
        "We can do this the easy way (now) or the embarrassing way (in tomorrow's recap).",
        "{name}: 60. Minutes. {missing} undone. "
        "The puzzles aren't long. The night is. Maths it.",
        "{name} — last hour. {missing}. "
        "I've seen you do harder things during meetings. Pretend this is a meeting.",
        "Hour to go, {name}. {missing}. "
        "Quick reminder: glory does not wait. Glory specifically does not wait.",
        "{name}: 1h, {missing}. "
        "This is your sign. This is THE sign. Go.",
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
        "{name}: 30 minutes. {missing}. "
        "I refuse to be the one tomorrow's recap pities. Move.",
        "Half an hour to redeem yourself, {name}. {missing}. "
        "Whatever the excuse is, save it for therapy.",
        "{name}, 30 mins. {missing} undone. "
        "This is the part of the movie where the hero finally does the thing.",
        "Thirty. Minutes. {name}. {missing}. "
        "The clock is doing its job. Are you?",
        "{name}, half hour. {missing}. "
        "I'm not begging. I'm pre-begging. Save us both the begging.",
        "30, {name}. {missing}. "
        "If you're driving: pull over. If you're cooking: turn off the stove. If you're scrolling: STOP.",
        "{name} — 30. {missing}. "
        "Pretend midnight is 11:30. Solve them. Surprise yourself.",
        "Half hour, {name}. {missing}. "
        "The window for dignified completion is narrowing.",
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
        "{name}, FIVE. {missing}. "
        "Fingers on phone. Eyes on screen. Brain on. NOW.",
        "{name} — 5 minutes. {missing}. "
        "I have begged. I have pleaded. I am now SHOUTING. PLAY.",
        "{name} this is the LAST PING. 5 minutes. {missing}. "
        "Tomorrow's recap is being written and it does NOT flatter you.",
        "{name}!! 5 MINUTES!! {missing}!! "
        "I CAN'T BE MORE EXPLICIT THAN THIS WITHOUT TRIGGERING SPAM FILTERS!!",
        "{name}, 300 seconds. {missing}. "
        "PUT. DOWN. THE PHONE. (Wait. Pick it up. Open LinkedIn. PLAY.)",
        "{name} the puzzles are about to expire and so is my patience. 5 min. {missing}. GO.",
        "{name}, this is your final boss fight. 5 minutes. {missing}. "
        "Win it. Lose it. Just don't ignore it.",
        "{name}: 5 minutes, {missing}. "
        "If you don't play these I will personally bring it up at every future recap until the heat death of the universe.",
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
    "Ding ding — new puzzles. Yesterday's heroes are today's fossils. "
    "Get in it.",
    "Today's drop has landed. Tango is judgmental, Queens is unforgiving, "
    "Zip is mean. Get in it.",
    "Puzzles up. Coffee in hand. Honour on the line. Get in it.",
    "Heads up — fresh games, blank slate, zero excuses. "
    "Whoever shares first gets bragging rights until lunch. Get in it.",
    "The puzzles are out and they're already laughing at you. "
    "Get in it before they get cocky.",
    "Brand new day, brand new puzzles, same old mediocrity unless you "
    "do something about it. Get in it.",
    "Reset complete. The leaderboard has been wiped of yesterday's sins. "
    "New games are LIVE — get in it.",
    "Morning. Puzzles dropped. The clock is running. Get in it.",
    "Today's puzzles: out. Today's leaderboard: empty. Today's vibe: opportunity. Get in it.",
    "The board has been wiped clean and the puzzles are warm. Get in it.",
    "Open. The. Puzzles. (Get in it.)",
    "Yesterday's standings have been carbon-dated. New games are LIVE. Get in it.",
    "Drumroll please — new puzzles. No drumroll? Fine. New puzzles anyway. Get in it.",
    "Daybreak. Puzzles up. Honour available in limited quantities. Get in it.",
    "The puzzles are out. Your move. Get in it.",
    "Today's games are LIVE and they are deeply judgmental. Get in it.",
)


_NEW_GAMES_CTX_LAST_DAY_TEMPLATES: Tuple[str, ...] = (
    # ``{period}`` interpolates "week" / "month" / "year".
    "Heads up — last day of the {period}. Standings lock at midnight.",
    "Final day of the {period}. No mulligans after this.",
    "{period} closes tonight. Make today count.",
    "Closing day of the {period} — your last word goes on the record.",
    "End-of-{period} energy required. Today's the deciding round.",
    "The {period} ends at LA midnight. Whatever you do today seals it.",
    "Last day of the {period}. The leaderboard is watching what you do.",
    "Final round of the {period}. Deliver or be remembered for not.",
)

_NEW_GAMES_CTX_BLOWOUT_TEMPLATES: Tuple[str, ...] = (
    # ``{name}`` and ``{game}`` and ``{gap}`` interpolated.
    "Yesterday {name} won {game} by {gap}. Set the bar high or get used to chasing.",
    "{name} smoked yesterday's {game} by {gap}. Today's a chance to remind them they're mortal.",
    "Reminder: {name} beat 2nd place on {game} by {gap} yesterday. The crown's right there.",
    "Yesterday's blowout — {name} took {game} by {gap}. Today: prove yesterday was a fluke.",
    "{name} won {game} by {gap} yesterday. The rest of you have some explaining to do.",
    "FYI {name} dropped {game} on the rest of you by {gap} yesterday. Avenge or accept.",
    "Yesterday: {name}, {game}, +{gap} on the field. Today: a fresh chance to humble them.",
)


def _new_games_last_day_context(today: date) -> Optional[str]:
    """Reuse the morning-nudge last-day detector but pull the rendered
    line from this surface's pool. Returns one line, or ``None`` when
    no period closes today."""
    if is_last_day_of_year(today):
        period = "year"
    elif is_last_day_of_month(today):
        period = "month"
    elif today.weekday() == 6:
        period = "week"
    else:
        return None
    template = random.choice(_NEW_GAMES_CTX_LAST_DAY_TEMPLATES)
    return template.format(period=period)


def _new_games_blowout_context(
    yesterday_scores: Sequence[ScoreRow], enabled_games: frozenset
) -> Optional[str]:
    """Find the largest 1st-vs-2nd time gap in yesterday's enabled
    games and return a "blowout" callout. Threshold: 30 seconds OR
    2x ratio — we only want clear blowouts, not 5-second wins."""
    by_game: Dict[str, List[ScoreRow]] = {}
    for s in yesterday_scores:
        if s.game not in enabled_games:
            continue
        by_game.setdefault(s.game, []).append(s)

    best_gap: int = 0
    best_winner: Optional[ScoreRow] = None
    best_game: Optional[str] = None
    for game, rows in by_game.items():
        if len(rows) < 2:
            continue
        sorted_rows = sorted(rows, key=lambda r: r.raw_score)
        winner, runner_up = sorted_rows[0], sorted_rows[1]
        gap = runner_up.raw_score - winner.raw_score
        # 30s absolute OR 2x ratio (whichever looser threshold).
        meaningful = (
            gap >= 30
            or (winner.raw_score > 0 and runner_up.raw_score >= winner.raw_score * 2)
        )
        if not meaningful:
            continue
        if gap > best_gap:
            best_gap = gap
            best_winner = winner
            best_game = game

    if best_winner is None or best_game is None:
        return None
    template = random.choice(_NEW_GAMES_CTX_BLOWOUT_TEMPLATES)
    minutes, seconds = divmod(best_gap, 60)
    if minutes:
        gap_str = f"{minutes}:{seconds:02d}"
    else:
        gap_str = f"{best_gap}s"
    return template.format(
        name=best_winner.player_name or "—",
        game=GAME_DISPLAY[best_game],
        gap=gap_str,
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

    A contextual riff (last day of period / yesterday's blowout) is
    prepended when relevant. If multiple riffs apply, one is picked
    at random — no priority order. Falls back to the bare static
    template when nothing fires.

    Returns the body that was sent, or ``None`` if no audience.
    """
    now = now or datetime.now(settings.tz)
    today = la_date(now)

    base = _NEW_GAMES_TEMPLATES[
        today.toordinal() % len(_NEW_GAMES_TEMPLATES)
    ]

    # Gather contextual riffs. ``yesterday_scores`` is bounded to the
    # day before today so the blowout detector sees only the puzzle
    # period that just closed.
    contexts: List[str] = []
    last_day = _new_games_last_day_context(today)
    if last_day is not None:
        contexts.append(last_day)
    try:
        yesterday = today - timedelta(days=1)
        yesterday_scores = repo.list_scores(date_from=yesterday, date_to=yesterday)
        blowout = _new_games_blowout_context(
            yesterday_scores, settings.enabled_games
        )
        if blowout is not None:
            contexts.append(blowout)
    except Exception:
        logger.exception(
            "New games blowout detection failed (LA day %s) — "
            "falling back to bare template", today,
        )

    if contexts:
        body = f"{random.choice(contexts)}\n\n{base}"
    else:
        body = base

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


# ---------------------------------------------------------------------------
# Easter egg: brag / gripe broadcasts (webhook-triggered)
# ---------------------------------------------------------------------------


# User-triggered broadcasts. ``brag`` for "I'm having a great day, let
# everybody know"; ``gripe`` for "I'm having a stinker, drag the group
# in with me". Per-player per-day cooldown lives in the repo so the
# same person can't spam the same prompt fifty times in a row.
TAUNT_KINDS: Tuple[str, ...] = ("brag", "gripe")

_BRAG_TEMPLATES: Tuple[str, ...] = (
    "{sender} is in form today and wanted you to know. Try to keep up.",
    "Public service announcement: {sender} is cooking. "
    "Maybe respond with scores. Maybe just suffer in silence.",
    "{sender} just hit the brag button. Translation: they're winning, "
    "they're insufferable, they want company.",
    "{sender} is having a moment and wants witnesses. Bear witness.",
    "Heads up — {sender} smells blood. The leaderboard is the blood.",
    "{sender} would like a small parade in their honour. "
    "Today's puzzles, apparently, were not for the weak.",
    "{sender} has typed `brag` into a chat bot. We are obligated to relay it. "
    "They're crushing it.",
    "Ding ding — {sender} is on a tear. Catch up or get used to second.",
    "{sender}: \"I'm having a great day.\" Bot: \"OK\". Group: forced to listen.",
    "{sender} is feeling themselves today. Admittedly, the data agrees.",
    "{sender} sent the bot an unsolicited flex. We are passing it on at retail.",
    "Notice: {sender} is undefeated in their own head right now. "
    "Provide receipts or accept defeat.",
)

_GRIPE_TEMPLATES: Tuple[str, ...] = (
    "{sender} bombed today and wants the group to feel it. "
    "Solidarity. Or mockery. Dealer's choice.",
    "{sender} is having a stinker. The bot is contractually obligated to tell you.",
    "{sender} requests recognition for the absolute mess they've made of today's puzzles.",
    "Heads up: {sender} is publicly admitting defeat. Use this information wisely.",
    "{sender} would like to file a complaint about today's puzzles. "
    "Complaint: they were too hard. Mostly for {sender}.",
    "{sender} is unwell (psychologically, at the puzzles). Send thoughts. "
    "Or send your scores so they can feel worse.",
    "{sender} has typed `gripe`. The bot dutifully relays: it's been a day. "
    "And not in the good way.",
    "Today defeated {sender} comprehensively, and they want everyone to know. "
    "Honesty appreciated.",
    "{sender} is having the kind of day where the puzzles are winning. "
    "By a lot. Comfort or mock at your discretion.",
    "Newsflash: {sender} is on the floor. Today did not go to plan. "
    "Tomorrow's revenge tour starts at midnight.",
    "{sender} sent the bot a distress signal. Roughly translated: \"I am bad at puzzles today.\" "
    "Group is invited to commiserate.",
    "{sender}: today's score was a personal worst. {sender}: needed you to know. "
    "{sender}: regrets nothing.",
)


def run_taunt(
    repo: Repository,
    settings: Settings,
    *,
    kind: str,
    sender_id: int,
    sender_name: str,
    sender_whatsapp_id: str,
    now: Optional[datetime] = None,
) -> Tuple[int, Optional[str]]:
    """Broadcast a ``brag`` or ``gripe`` to every recently-active
    player except the sender.

    Returns ``(count_sent, body)`` where ``count_sent`` is the number
    of recipients Twilio accepted and ``body`` is the rendered text
    (handy for tests / logs). Returns ``(0, None)`` when the sender
    is on cooldown for ``kind`` today, and ``(0, body)`` when nobody
    else is in the active window.

    Cooldown is recorded **only** when the broadcast actually goes
    out — if there's no audience, the sender doesn't burn their
    daily token.
    """
    if kind not in TAUNT_KINDS:
        raise ValueError(f"unknown taunt kind: {kind!r}")
    now = now or datetime.now(settings.tz)
    today = la_date(now)

    if repo.has_taunted_today(sender_id, kind, today):
        return 0, None

    pool = _BRAG_TEMPLATES if kind == "brag" else _GRIPE_TEMPLATES
    body = pool[today.toordinal() % len(pool)].format(sender=sender_name)

    since = today - timedelta(days=_ACTIVE_WINDOW_DAYS)
    targets = [
        wid for wid in repo.list_active_whatsapp_ids(date_from=since, date_to=today)
        if wid != sender_whatsapp_id
    ]
    if not targets:
        return 0, body

    sent = 0
    for wid in targets:
        if send_dm(settings, wid, body):
            sent += 1
    repo.record_taunt(sender_id, kind, today)
    logger.info(
        "Taunt [%s] from player_id=%s reached %d/%d recipients (LA day %s)",
        kind, sender_id, sent, len(targets), today,
    )
    return sent, body
