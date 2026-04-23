"""Webhook business logic — pure and fully testable without FastAPI.

:func:`handle_inbound` accepts the three Twilio fields we care about plus
an injected :class:`~app.db.Repository` and returns the bot's reply text,
or ``None`` to stay silent (so the bot doesn't spam a group chat with
help text every time someone says "hey").

Reply / silence matrix:

- empty / whitespace body ........................ **silent**
- random chatter (no game name + no ``lnkd.in/``) . **silent**
- score-like text that fails to parse ............ reply + log (we want
  feedback when a real share gets mangled)
- valid score, wrong puzzle number ............... reply (reject)
- valid score, duplicate ......................... reply (reject)
- valid score, fresh ............................. reply (confirm)
- ``stats`` / ``unparsed`` commands .............. reply

Commands (case-insensitive):
- ``stats`` — reply with the sender's all-time per-game stats.
- ``unparsed`` — reply with the last 10 unparsed messages (admin debug).
- ``recap`` / ``today`` — render the daily recap for the current
  in-progress LA day (partial if midday). Lets a user pull the
  "where are we up to" view from their phone.
- ``wrap`` / ``week`` — render the weekly wrap for the current
  in-progress LA week.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Callable, Dict, FrozenSet, List, Optional

from .config import Settings
from .db import Repository
from .parsers import (
    GAME_DISPLAY,
    GAME_DISPLAY_ORDER,
    GAMES,
    format_raw_score,
    looks_like_score,
    parse_any,
)
from .puzzles import la_date

# Max look-back for the "N days ago" command. Cap at 6 so users can
# still grab any day within the current week (Mon–Sat from Sunday)
# but can't accidentally ask for a long-closed week.
_MAX_DAYS_AGO = 6

# Matches "N days ago" / "N day ago" / "N days" — tolerant of
# pluralisation and trailing ``ago``.
_DAYS_AGO_RE = re.compile(r"^(\d+)\s+days?(\s+ago)?$", re.IGNORECASE)

# Matches "recap YYYY-MM-DD" for exact-date pulls.
_RECAP_DATE_RE = re.compile(
    r"^recap\s+(\d{4}-\d{2}-\d{2})$", re.IGNORECASE
)


# Compact help message. Used by the ``help`` / ``?`` command and as
# the fallback reply when the bot can't classify a message. Kept
# short enough to fit a single WhatsApp bubble without scrolling.
_HELP_TEXT = (
    "Commands:\n"
    "  stats — your all-time stats & personal bests\n"
    "  recap / today — today's recap\n"
    "  yesterday — yesterday's recap\n"
    "  \"3 days ago\" — any day this week (1–6)\n"
    "  recap YYYY-MM-DD — a specific day\n"
    "  week / wrap — this week's wrap\n"
    "  all / history — every round this week\n"
    "  help / ? — show this list\n"
    "\n"
    "Submit a score by pasting the LinkedIn share text, e.g.:\n"
    "  Queens #714\n"
    "  0:10"
)

# Format hint when a message looks score-ish but didn't parse. Doesn't
# dump the full command list — the user clearly meant to submit a score.
_SCORE_FORMAT_HINT = (
    "Expected a LinkedIn share like:\n"
    "  Queens #714\n"
    "  0:10"
)


def _resolve_recap_target(
    lower: str, today: date
) -> Optional[tuple[date, Optional[str]]]:
    """Parse a recap-style command into a target date.

    Returns ``(target_day, error_msg)``. ``error_msg`` is ``None`` on
    success; if set, the command matched shape but the date was out of
    range and the caller should reply with that explanation. Returns
    ``None`` when the text isn't a recap command at all (so the main
    handler can try other commands or fall through to score parsing).

    Supported forms:
    - ``recap`` / ``today`` → today
    - ``yesterday`` → today - 1
    - ``N days ago`` (N = 1..6) → today - N
    - ``recap YYYY-MM-DD`` → exact date within the last week
    """
    if lower in ("recap", "today"):
        return (today, None)
    if lower == "yesterday":
        return (today - timedelta(days=1), None)

    m = _DAYS_AGO_RE.match(lower)
    if m:
        n = int(m.group(1))
        if n < 1 or n > _MAX_DAYS_AGO:
            return (
                today,
                f"I can only pull recaps from the last {_MAX_DAYS_AGO} days. "
                f"Try ``yesterday``, ``2 days ago`` … up to "
                f"``{_MAX_DAYS_AGO} days ago``.",
            )
        return (today - timedelta(days=n), None)

    m = _RECAP_DATE_RE.match(lower)
    if m:
        try:
            target = date.fromisoformat(m.group(1))
        except ValueError:
            return (today, "Couldn't parse that date. Use YYYY-MM-DD.")
        if target > today:
            return (today, "That's in the future — I don't have those scores yet.")
        if (today - target).days > _MAX_DAYS_AGO:
            return (
                today,
                f"I can only pull recaps from the last {_MAX_DAYS_AGO} days.",
            )
        return (target, None)

    return None


# ---------------------------------------------------------------------------
# /stats command
# ---------------------------------------------------------------------------


def _handle_stats(repo: Repository, from_: str, profile_name: str) -> str:
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    scores = repo.list_player_scores(player.id)

    if not scores:
        return "No scores recorded yet. Submit a LinkedIn game share to get started!"

    distinct_games = {s.game for s in scores}
    distinct_days = {s.puzzle_date for s in scores}

    # Per-game best
    best: Dict[str, int] = {}
    count: Dict[str, int] = {}
    for s in scores:
        count[s.game] = count.get(s.game, 0) + 1
        if s.game not in best or s.raw_score < best[s.game]:
            best[s.game] = s.raw_score

    lines: List[str] = [
        f"Stats for {player.display_name}:",
        f"  Submissions: {len(scores)}",
        f"  Games played: {len(distinct_games)}/7",
        f"  Days active: {len(distinct_days)}",
        "",
        "Personal bests:",
    ]
    for game in GAME_DISPLAY_ORDER:
        if game in best:
            lines.append(
                f"  {GAME_DISPLAY[game]}: "
                f"{format_raw_score(game, best[game])} "
                f"({count[game]} submissions)"
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /unparsed command
# ---------------------------------------------------------------------------


def _handle_unparsed(repo: Repository) -> str:
    entries = repo.list_recent_unparsed(limit=10)
    if not entries:
        return "No unparsed messages in the log."

    lines = [f"Recent unparsed messages ({len(entries)}):"]
    for i, entry in enumerate(entries, start=1):
        who = entry.get("whatsapp_id", "?")
        body = entry.get("body", "")
        # Truncate long bodies for readability
        preview = body[:80] + ("..." if len(body) > 80 else "")
        lines.append(f"  {i}. {who}: {preview}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


def _handle_recap(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    target_day: Optional[date] = None,
) -> str:
    """On-demand daily recap for ``target_day`` (defaults to today LA)."""
    if settings is None:
        return "Recap isn't available in this context."
    # Lazy import to avoid a circular dep (jobs imports scheduler which
    # imports scoring which imports db — none of that touches webhook,
    # but the webhook module is imported early by main.py).
    from .jobs import render_daily

    day = target_day or la_date(now)
    body, _ = render_daily(repo, settings, day)
    return body


def _handle_all_week(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
) -> str:
    """Every round this week, day by day.

    Builds a compact per-day per-game block Monday-through-today. Lets
    users see the whole week at a glance without scrolling back
    through multiple daily recaps.
    """
    if settings is None:
        return "Week summary isn't available in this context."
    from .puzzles import week_bounds
    from .scheduler import _per_game_sections

    today = la_date(now)
    monday, sunday = week_bounds(today)
    week_scores = repo.list_scores(date_from=monday, date_to=sunday)
    week_filtered = [
        s for s in week_scores if s.game in settings.enabled_games
    ]

    if not week_filtered:
        return "No scores yet this week."

    header = (
        f"Week so far — "
        f"{monday.strftime('%a %d %b')} to {today.strftime('%a %d %b %Y')}"
    )
    lines: List[str] = [header, ""]

    # Iterate Mon → today; skip empty days so the output stays tight.
    cursor = monday
    while cursor <= today:
        day_scores = [s for s in week_filtered if s.puzzle_date == cursor]
        if day_scores:
            lines.append(cursor.strftime("%a %d %b:"))
            lines.extend(_per_game_sections(cursor, day_scores))
            lines.append("")
        cursor += timedelta(days=1)

    return "\n".join(lines).rstrip() + "\n"


def _handle_wrap(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
) -> str:
    """On-demand weekly wrap for the current in-progress LA week."""
    if settings is None:
        return "Wrap isn't available in this context."
    from .jobs import render_wrap

    reference_day = la_date(now)
    body, _ = render_wrap(repo, settings, reference_day)
    return body


def handle_inbound(
    repo: Repository,
    *,
    from_: str,
    body: str,
    profile_name: str,
    now: datetime,
    enabled_games: FrozenSet[str] = frozenset(GAMES),
    expected_puzzle_no: Optional[Callable[[str, datetime], int]] = None,
    settings: Optional[Settings] = None,
) -> Optional[str]:
    """Process an inbound WhatsApp message.

    Returns the bot's reply text, or ``None`` to stay silent — used when
    the message is plain chatter that shouldn't be acknowledged (so the
    bot doesn't post help text into a group every time someone says hi).

    ``expected_puzzle_no``, if provided, is called as
    ``expected_puzzle_no(game, now)`` and returns the puzzle number
    LinkedIn is currently serving for that game. When the submitted
    ``puzzle_no`` doesn't match, the handler rejects the submission with
    a helpful message explaining the LA-midnight rollover. Passing
    ``None`` (the default) skips validation — used by unit tests so they
    can exercise the handler with arbitrary puzzle numbers.

    ``settings`` is required for the ``recap`` / ``wrap`` commands
    (they need the ``enabled_games`` frozenset + tz for rendering).
    When ``None``, those commands reply with a short explanation.
    """
    body_stripped = (body or "").strip()
    if not body_stripped:
        return None

    # Check for commands before attempting score parsing
    lower = body_stripped.lower()
    if lower in ("help", "?", "commands"):
        return _HELP_TEXT
    if lower == "stats":
        return _handle_stats(repo, from_, profile_name)
    if lower == "unparsed":
        return _handle_unparsed(repo)
    if lower in ("wrap", "week"):
        return _handle_wrap(repo, settings, now)
    if lower in ("all", "all week", "history"):
        return _handle_all_week(repo, settings, now)

    # Date-anchored recap commands: ``recap`` / ``today`` / ``yesterday``
    # / ``N days ago`` / ``recap YYYY-MM-DD``. Consolidated into one
    # resolver so the handler doesn't grow a branch per phrasing.
    recap_target = _resolve_recap_target(lower, la_date(now))
    if recap_target is not None:
        target_day, error = recap_target
        if error is not None:
            return error
        return _handle_recap(repo, settings, now, target_day=target_day)

    # Try to parse as a game share
    parsed = parse_any(body_stripped)

    if parsed is None:
        if looks_like_score(body_stripped):
            repo.log_unparsed(from_, body_stripped)
            return (
                "That looks like a LinkedIn game share but I couldn't read "
                f"it — logged for a parser fix.\n\n{_SCORE_FORMAT_HINT}"
            )
        # Doesn't look like a score and doesn't match a command — reply
        # with a help blurb so users aren't left guessing. (The bot
        # operates in 1:1 DMs, so this won't spam a group chat.)
        return f"I didn't understand that.\n\n{_HELP_TEXT}"

    pretty_game = GAME_DISPLAY[parsed.game]

    # Reject stale/future puzzle numbers. LinkedIn rolls puzzles at
    # midnight US Pacific, so ``expected_puzzle_no`` uses LA time to pick
    # today's live number regardless of where the submitter lives.
    if expected_puzzle_no is not None:
        expected = expected_puzzle_no(parsed.game, now)
        if parsed.puzzle_no != expected:
            if parsed.puzzle_no < expected:
                when = "yesterday" if parsed.puzzle_no == expected - 1 else "an older day"
            else:
                when = "tomorrow" if parsed.puzzle_no == expected + 1 else "a future day"
            return (
                f"That's {pretty_game} #{parsed.puzzle_no} ({when}'s puzzle). "
                f"Today's {pretty_game} is #{expected} — I can only record "
                "today's scores. (LinkedIn resets at midnight US Pacific.)"
            )

    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    # Anchor the puzzle day in LA time — that's when LinkedIn rolls, so a
    # 4:45pm Sydney submission (still yesterday in LA) files under
    # yesterday's LA date and a 5:15pm one lands under today's. Keeps
    # daily/weekly windows consistent with LinkedIn's own puzzle days.
    puzzle_date = la_date(now)

    inserted = repo.insert_score(
        player_id=player.id,
        game=parsed.game,
        puzzle_no=parsed.puzzle_no,
        puzzle_date=puzzle_date,
        raw_score=parsed.raw_score,
        share_text=parsed.share_text,
    )

    pretty_new = format_raw_score(parsed.game, parsed.raw_score)

    if not inserted:
        existing_raw = repo.get_existing_score(
            player_id=player.id,
            game=parsed.game,
            puzzle_no=parsed.puzzle_no,
        )
        if existing_raw is not None:
            pretty_existing = format_raw_score(parsed.game, existing_raw)
            return (
                f"You already submitted {pretty_game} #{parsed.puzzle_no} "
                f"with {pretty_existing}. "
                f"This attempt ({pretty_new}) was not recorded."
            )
        return (
            f"You already submitted {pretty_game} #{parsed.puzzle_no}. "
            f"This attempt ({pretty_new}) was not recorded."
        )

    off_note = ""
    if parsed.game not in enabled_games:
        off_note = " (Not tracked for the leaderboard.)"

    return (
        f"Got it, {player.display_name}. "
        f"{pretty_game} #{parsed.puzzle_no}: {pretty_new}.{off_note}"
    )
