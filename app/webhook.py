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
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

from .config import Settings
from .db import Group, Repository, ScoreRow
from .parsers import (
    GAME_DISPLAY,
    GAME_DISPLAY_ORDER,
    GAMES,
    format_raw_score,
    looks_like_score,
    parse_any,
)
from .puzzles import la_date, month_bounds, week_bounds, year_bounds

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

# Strip an optional leading ``leaderboard`` / ``standings`` prefix so
# ``leaderboard yesterday`` parses the same as ``yesterday`` for
# date-keyword resolution. Kept separate from the recap parser so
# ``recap yesterday`` can still yield the full recap while bare
# ``yesterday`` yields just the leaderboard.
_LEADERBOARD_PREFIX_RE = re.compile(
    r"^(leaderboard|standings)\s+", re.IGNORECASE
)


# Compact help message. Used by the ``help`` / ``?`` command and as
# the fallback reply when the bot can't classify a message. Grouped
# into Read / You / Mutating sections so the wall of commands is
# scannable — we have a lot now.
_HELP_TEXT = (
    "Commands (send `ultrahelp` for full detail):\n"
    "  Scores:    recap · leaderboard · week · month · year · times\n"
    "  Records:   records <game> · dow <game>\n"
    "  Global:    global · global recap · global week · global times\n"
    "  You:       stats · pb · streak · vs · history · trends · pace\n"
    "             best day · worst day · by day\n"
    "  Setup:     group · switch · name · notify · undo · track\n"
    "  Misc:      rules · prizes · missing · games\n"
    "  Fun:       brag · gripe · nag · 42\n"
    "\n"
    "Paste a LinkedIn share text to submit a score."
)

_ULTRA_HELP_TEXT = (
    "Full command reference:\n"
    "\n"
    "  Group:\n"
    "    group <name> — create a new group or join an existing one\n"
    "    switch <name> — move to a different existing group (past scores stay)\n"
    "    track <game> [game...] — set which games count for your group\n"
    "    track all — track all available games\n"
    "    track reset — revert to the global default game set\n"
    "    games — show which games your group is currently tracking\n"
    "\n"
    "  Leaderboards:\n"
    "    leaderboard — full weekly standings\n"
    "    leaderboard <game> — single-game standings (e.g. \"leaderboard queens\")\n"
    "    leaderboard <g1> <g2> ... — multi-game (e.g. \"leaderboard zip tango\")\n"
    "    leaderboard yesterday / leaderboard YYYY-MM-DD — past standings\n"
    "    times — per-game fastest totals this week\n"
    "    month / mtd (+ optional game, e.g. \"month queens\") — MTD summary\n"
    "    year / ytd (+ optional game, e.g. \"year queens\") — YTD summary\n"
    "    records <game> — all-time top 3 raw scores for one game (e.g. \"records queens\")\n"
    "    dow <game> — best score per day of week this year (e.g. \"dow queens\")\n"
    "\n"
    "  Global (across all groups, common games only):\n"
    "    global — combined leaderboard this week\n"
    "    global <game> — global leaderboard filtered to one game\n"
    "    global recap / global yesterday — cross-group daily recap\n"
    "    global <YYYY-MM-DD> — cross-group recap for a specific date\n"
    "    global week / global wrap — cross-group weekly wrap\n"
    "    global times — cross-group fastest times this week\n"
    "\n"
    "  Recaps & summaries:\n"
    "    recap / today — full daily recap\n"
    "    yesterday / \"N days ago\" / recap YYYY-MM-DD — past daily recap\n"
    "    week / wrap — weekly wrap\n"
    "    story / narrative — narrative paragraph recap of the week\n"
    "    all week — every score submitted this week\n"
    "    prizes — live prize snapshot\n"
    "    missing / who — who hasn't played today\n"
    "    rules / scoring — how points work\n"
    "\n"
    "  About you:\n"
    "    stats — your all-time stats (includes podium streak)\n"
    "    pb / bests — your personal bests across all games\n"
    "    streak — your current submission streak\n"
    "    vs <name> — head-to-head record against another player\n"
    "    history — your recent scores across all games (last 30 days)\n"
    "    history <game> — your last 20 scores for one game, PBs marked\n"
    "    trends — how your averages this month compare to last month\n"
    "    best day — your highest-scoring composite day ever\n"
    "    worst day — your lowest-scoring composite day ever\n"
    "    pace — your current rank and projected points by Sunday\n"
    "    estimate — what score you need in each game to overtake the leader\n"
    "    by day — your day-of-week breakdown (all games)\n"
    "    by day <game> — day-of-week breakdown for one game\n"
    "    by day <game> <N> — same, using only your last N plays\n"
    "\n"
    "  Change things:\n"
    "    undo — delete today's last submission\n"
    "    name <new> — change your display name\n"
    "    notify on / notify off — toggle daily recap DMs\n"
    "\n"
    "  Easter eggs (once each per day):\n"
    "    brag / flex — taunt the group that you're crushing it\n"
    "    gripe / whinge — taunt the group that today's a write-off\n"
    "    nag / blast / poke — DM everyone who hasn't played today\n"
    "    42 — unlock founder lore\n"
    "\n"
    "Submit a score by pasting the LinkedIn share text, e.g.:\n"
    "  Queens #714\n"
    "  0:10"
)

# Help text shown when an un-onboarded sender runs ``help``. Kept
# short — the only useful command before joining a group is the
# join command itself.
_ONBOARDING_HELP_TEXT = (
    "Welcome! You haven't joined a group yet.\n"
    "\n"
    "  group <name> — create a new group or join an existing one\n"
    "  help / ? — show this message\n"
    "\n"
    "Group names are case-insensitive. Pick one your friends "
    "agreed on, then everyone runs `group <name>` to join."
)

# Maximum length for a group name. Generous enough for any real
# friend-group nickname, short enough that it can't be used as a
# storage abuse vector.
_MAX_GROUP_NAME_LENGTH = 40

# Group-related command parsers. Both verbs (``group`` and
# ``switch``) accept a single name argument; case is preserved on
# create so the original spelling shows up in replies.
_GROUP_RE = re.compile(r"^group\s+(\S.*)$", re.IGNORECASE)
_SWITCH_RE = re.compile(r"^switch\s+(\S.*)$", re.IGNORECASE)

# Format hint when a message looks score-ish but didn't parse. Doesn't
# dump the full command list — the user clearly meant to submit a score.
_SCORE_FORMAT_HINT = (
    "Expected a LinkedIn share like:\n"
    "  Queens #714\n"
    "  0:10"
)

# Hidden Easter egg — DM ``42`` to unlock. Verbatim text supplied by
# the creator. Don't reformat it; the line breaks and trailing
# two-space soft-breaks are intentional.
_EASTER_EGG_42 = """Easter Egg: Founder Lore (Unlocked)

Hi, I'm David.

Actuary by trade — which means I professionally think about risk, probabilities, and what could go wrong… and then explain it in spreadsheets.

I've spent my career across life insurance, reinsurance, and consulting — doing very serious things with very serious people (AIA, Hannover Re, etc.), usually involving long documents and longer meetings.

Somewhere along the way, I picked up a hobby in trading futures.

Built systems. Tested ideas. Launched a fund. Closed a fund.
Net result: a healthy respect for markets and an unhealthy number of Excel tabs.

I also write — partly to clarify my own thinking, partly because once you start having opinions about insurance, it's hard to stop.

Outside of all that:
- Married Fazzy (still not sure how I pulled that off)
- Dad to Jamie, Issy, and Livy
- Now operating on a sleep schedule designed by small children

This app is probably the most "me" thing I've built.

Not a corporate initiative.
Not a client deliverable.
Just something slightly unnecessary, mildly over-engineered, and very satisfying.

A LinkedIn games tracker. With WhatsApp. Of course.

It's the kind of thing I'd be proud to show my kids one day — not because it changes the world, but because I made it.

If you've found this, you're either:
(a) a good friend
(b) curious enough to dig
(c) avoiding something more important

…or all three.

Welcome."""


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
    - ``recap yesterday`` / ``yesterday`` → today - 1
    - ``recap N days ago`` / ``N days ago`` (N = 1..6) → today - N
    - ``recap YYYY-MM-DD`` → exact date within the last week

    The bare ``yesterday`` / ``N days ago`` keywords route to the full
    recap so a casual "what happened yesterday?" gets the per-game
    breakdown people actually want. The compact-leaderboard variant
    is still available behind the explicit ``leaderboard …`` /
    ``standings …`` prefix (see :func:`_resolve_leaderboard_target`).
    """
    if lower in ("recap", "today"):
        return (today, None)
    if lower in ("recap yesterday", "yesterday"):
        return (today - timedelta(days=1), None)

    # ``recap 3 days ago`` / ``recap 3 days`` / bare ``3 days ago``.
    rest = lower[len("recap "):] if lower.startswith("recap ") else lower
    m = _DAYS_AGO_RE.match(rest)
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


def _resolve_leaderboard_target(
    lower: str, today: date
) -> Optional[tuple[date, Optional[str]]]:
    """Parse a leaderboard-style date command into a target date.

    Returns the same ``(target_day, error_msg)`` shape as
    :func:`_resolve_recap_target`; returns ``None`` when the text
    isn't an explicitly-prefixed leaderboard command.

    Only the explicit ``leaderboard …`` / ``standings …`` form lands
    here — bare ``yesterday`` / ``N days ago`` route to the full
    recap (see :func:`_resolve_recap_target`) since that's what
    people usually want. The compact leaderboard view is reserved
    for callers who explicitly ask for it.

    Supported forms:
    - ``leaderboard yesterday`` → today - 1
    - ``leaderboard N days ago`` (N = 1..6) → today - N
    - ``leaderboard YYYY-MM-DD`` → exact date within the last week
    """
    # Require the leaderboard/standings prefix — without it, the bare
    # keyword belongs to the recap resolver.
    stripped = _LEADERBOARD_PREFIX_RE.sub("", lower, count=1)
    if stripped == lower:
        return None

    if stripped == "yesterday":
        return (today - timedelta(days=1), None)

    m = _DAYS_AGO_RE.match(stripped)
    if m:
        n = int(m.group(1))
        if n < 1 or n > _MAX_DAYS_AGO:
            return (
                today,
                f"I can only pull leaderboards from the last {_MAX_DAYS_AGO} days. "
                f"Try ``leaderboard yesterday``, ``leaderboard 2 days ago`` … up to "
                f"``leaderboard {_MAX_DAYS_AGO} days ago``.",
            )
        return (today - timedelta(days=n), None)

    try:
        target = date.fromisoformat(stripped)
    except ValueError:
        return None
    if target > today:
        return (today, "That's in the future — I don't have those scores yet.")
    if (today - target).days > _MAX_DAYS_AGO:
        return (
            today,
            f"I can only pull leaderboards from the last {_MAX_DAYS_AGO} days.",
        )
    return (target, None)


def _parse_leaderboard_games(lower: str) -> Optional[List[str]]:
    """Parse ``leaderboard game1 game2 ...`` into a list of game keys.

    Returns ``None`` when the prefix is missing, fewer than two words
    follow, or any word isn't a recognised game name. Single-game
    variants are intentionally excluded (handled by the per-game loop).
    """
    m = _LEADERBOARD_PREFIX_RE.match(lower)
    if m is None:
        return None
    rest = lower[m.end():].strip()
    if not rest:
        return None
    words = rest.split()
    if len(words) < 2:
        return None
    display_to_key = {GAME_DISPLAY[k].lower(): k for k in GAMES}
    resolved: List[str] = []
    for w in words:
        if w in GAMES:
            resolved.append(w)
        elif w in display_to_key:
            resolved.append(display_to_key[w])
        else:
            return None
    return resolved


def _parse_game_names_from_words(words: List[str]) -> Optional[List[str]]:
    """Convert a list of words into game keys. Returns ``None`` if any word
    isn't a recognised game name; returns ``[]`` for an empty list.

    Uses longest-match so multi-word names like "mini sudoku" are matched
    before falling back to single words.
    """
    if not words:
        return []
    display_to_key = {GAME_DISPLAY[k].lower(): k for k in GAMES}
    resolved: List[str] = []
    i = 0
    while i < len(words):
        matched = False
        for length in range(len(words) - i, 0, -1):
            phrase = " ".join(words[i : i + length])
            if phrase in GAMES:
                resolved.append(phrase)
                i += length
                matched = True
                break
            elif phrase in display_to_key:
                resolved.append(display_to_key[phrase])
                i += length
                matched = True
                break
        if not matched:
            return None
    return resolved


# ---------------------------------------------------------------------------
# /stats command
# ---------------------------------------------------------------------------


def _handle_stats(
    repo: Repository, from_: str, profile_name: str, *, group_id: int,
    settings: Optional["Settings"] = None, now: Optional["datetime"] = None,
) -> str:
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    scores = repo.list_player_scores(player.id, group_id=group_id)

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

    # Podium streak.
    if settings is not None and now is not None:
        try:
            from .stats import podium_streak as _podium_streak
            from .puzzles import week_bounds as _wb, la_date as _la
            monday, _ = _wb(_la(now))
            streak = _podium_streak(
                repo,
                player_id=player.id,
                group_id=group_id,
                reference_week_monday=monday,
                enabled_games=settings.enabled_games,
            )
            if streak > 0:
                week_word = "week" if streak == 1 else "weeks"
                lines.append("")
                lines.append(f"Podium streak: {streak} {week_word} in the top 3")
        except Exception:
            pass

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


def _games_played_today_by(
    repo: Repository,
    whatsapp_id: str,
    profile_name: str,
    today_la: date,
    enabled_games: FrozenSet[str],
    *,
    group_id: int,
) -> set[str]:
    """Set of enabled games the sender has submitted on the LA day
    ``today_la``. Used by the no-peek gate to decide whether a
    requester gets to see today's competitive data — see
    :func:`_handle_recap` and :func:`_handle_leaderboard`."""
    display_name = (profile_name or "").strip() or whatsapp_id
    player = repo.get_or_create_player(whatsapp_id, display_name)
    today_scores = repo.list_scores(
        date_from=today_la, date_to=today_la, group_id=group_id
    )
    return {
        s.game for s in today_scores
        if s.player_id == player.id and s.game in enabled_games
    }


def _pretty_game_list(games: "set[str] | frozenset[str]") -> str:
    """Render a set of game keys in canonical display order."""
    return ", ".join(GAME_DISPLAY[g] for g in GAME_DISPLAY_ORDER if g in games)


def _no_peek_zero_recap(enabled_games: FrozenSet[str]) -> str:
    """Reply when a requester asks for today's recap without having
    submitted a single game. LinkedIn-style lockout."""
    return (
        "No peeking. Submit at least one of today's games first, then "
        "ask again.\n"
        "\n"
        f"Today's games: {_pretty_game_list(enabled_games)}"
    )


def _no_peek_leaderboard(
    played: "set[str]", enabled_games: FrozenSet[str]
) -> str:
    """Reply when a requester asks for today's leaderboard without
    having submitted every enabled game. Stricter than the recap
    gate — the leaderboard aggregates everyone's scores so partial
    submitters get no view."""
    missing = set(enabled_games) - played
    return (
        "No peeking. The leaderboard unlocks when you've played every "
        "game today.\n"
        "\n"
        f"Played today: {_pretty_game_list(played) or '(none)'}\n"
        f"Still to play: {_pretty_game_list(missing)}"
    )


def _partial_peek_recap(
    repo: Repository,
    settings: Settings,
    today: date,
    played: "set[str]",
    enabled_games: FrozenSet[str],
    *,
    group_id: int,
) -> str:
    """Render today's recap restricted to the games the requester has
    played, with all aggregate blocks (week-so-far leaderboard,
    per-game running totals, month/year totals, missing-today nag)
    suppressed. Tail-appends a 'still to play' note so the requester
    knows what unlocks the rest."""
    from .scheduler import daily_recap

    monday, sunday = week_bounds(today)
    week_scores = repo.list_scores(
        date_from=monday, date_to=sunday, group_id=group_id
    )
    body = daily_recap(
        today, week_scores,
        enabled_games=frozenset(played),
        include_missing_today_nag=False,
        lock_aggregates=True,
    ).rstrip()
    missing = set(enabled_games) - played
    return (
        f"{body}\n\n"
        f"Still to play: {_pretty_game_list(missing)}\n"
        "Submit those to unlock the leaderboard and the full recap."
    )


def _handle_recap(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    target_day: Optional[date] = None,
    *,
    from_: Optional[str] = None,
    profile_name: str = "",
    group_id: int,
) -> str:
    """On-demand daily recap for ``target_day`` (defaults to today LA).

    For today's recap, gates the response on the requester's own
    submissions to stop pre-attempt peeking (LinkedIn-style):
    - 0 games submitted → terse no-peek reply
    - some submitted → recap restricted to those games, no aggregates
    - all submitted → full recap (current behavior)

    Past-day recaps are never gated — the requester is reading
    history, not peeking at a live round.
    """
    if settings is None:
        return "Recap isn't available in this context."
    # Lazy import to avoid a circular dep (jobs imports scheduler which
    # imports scoring which imports db — none of that touches webhook,
    # but the webhook module is imported early by main.py).
    from .jobs import render_daily

    today = la_date(now)
    day = target_day or today

    # No-peek gate: only on today's recap, only when we know who's
    # asking. ``from_=None`` is used by some test paths and by the
    # CLI preview, where gating doesn't apply.
    if day == today and from_ is not None and settings.enabled_games:
        played = _games_played_today_by(
            repo, from_, profile_name, today, settings.enabled_games,
            group_id=group_id,
        )
        all_games = set(settings.enabled_games)
        if not played:
            return _no_peek_zero_recap(settings.enabled_games)
        if played != all_games:
            return _partial_peek_recap(
                repo, settings, today, played, settings.enabled_games,
                group_id=group_id,
            )
        # All games played → fall through to the full recap below.

    # Past-day recaps drop the "Haven't heard from X today" footer —
    # nagging about a missed Tuesday from inside a Friday recap reads
    # as nonsense. The current-day recap keeps it.
    body, _ = render_daily(
        repo, settings, day,
        include_missing_today_nag=(day == today),
        group_id=group_id,
        now=now,
    )
    return body


def _handle_all_week(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    *,
    group_id: int,
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
    week_scores = repo.list_scores(
        date_from=monday, date_to=sunday, group_id=group_id
    )
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
    *,
    group_id: int,
) -> str:
    """On-demand weekly wrap for the current in-progress LA week."""
    if settings is None:
        return "Wrap isn't available in this context."
    from .jobs import render_wrap

    reference_day = la_date(now)
    body, _ = render_wrap(repo, settings, reference_day, group_id=group_id, now=now)
    return body


# ---------------------------------------------------------------------------
# Read-only utility commands (added in the "do it all" batch)
# ---------------------------------------------------------------------------


def _fmt_pts(value: float) -> str:
    """Compact points rendering: ``5`` for ints, ``5.8`` for fractions."""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}"


def _week_filtered_scores(
    repo: Repository, settings: Settings, now: datetime, *, group_id: int
) -> Tuple[List[ScoreRow], date, date, date]:
    """Common prologue for week-scoped commands.

    Fetches the whole current LA week, filters to enabled games, and
    returns ``(filtered_scores, today, monday, sunday)``.
    """
    from .puzzles import week_bounds

    today = la_date(now)
    monday, sunday = week_bounds(today)
    week_scores = repo.list_scores(
        date_from=monday, date_to=sunday, group_id=group_id
    )
    filtered = [s for s in week_scores if s.game in settings.enabled_games]
    return filtered, today, monday, sunday


def _render_per_game_leaderboard(
    scores: List[ScoreRow], game: str, title: str
) -> Optional[str]:
    """Shared per-game leaderboard renderer. ``scores`` is the
    filtered (enabled + in-scope) slice for the period — week,
    month, or year. Returns ``None`` when no rows matched ``game``
    so callers can emit their own "no scores" message."""
    from .scheduler import _format_seconds
    from .scoring import _NON_TIME_GAMES, assign_daily_points

    groups: Dict[int, List[ScoreRow]] = {}
    player_names: Dict[int, str] = {}
    for s in scores:
        if s.game != game:
            continue
        groups.setdefault(s.puzzle_no, []).append(s)
        player_names[s.player_id] = s.player_name
    if not groups:
        return None

    totals: Dict[int, float] = {}
    submissions: Dict[int, int] = {}
    time_total: Dict[int, int] = {}
    for group_scores in groups.values():
        for pid, pts in assign_daily_points(group_scores).items():
            totals[pid] = totals.get(pid, 0.0) + pts
        for s in group_scores:
            submissions[s.player_id] = submissions.get(s.player_id, 0) + 1
            if s.game not in _NON_TIME_GAMES:
                time_total[s.player_id] = time_total.get(s.player_id, 0) + s.raw_score

    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    is_time_game = game not in _NON_TIME_GAMES
    lines = [title]
    for i, (pid, pts) in enumerate(ranked, start=1):
        subs = submissions.get(pid, 0)
        if is_time_game:
            stats = f"T: {_format_seconds(time_total.get(pid, 0))}, G:{subs}"
        else:
            stats = f"G:{subs}"
        lines.append(
            f"  {i}. {player_names[pid]}: "
            f"{_fmt_pts(round(pts, 1))} pts ({stats})"
        )
    return "\n".join(lines)


def _handle_leaderboard(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    game: Optional[str] = None,
    target_day: Optional[date] = None,
    *,
    from_: Optional[str] = None,
    profile_name: str = "",
    group_id: int,
) -> str:
    """Weekly leaderboard — overall or restricted to one game.

    ``target_day`` scopes the standings "as of" a specific LA date —
    passing yesterday gives yesterday's end-of-day leaderboard, etc.
    Defaults to today LA. Scores after ``target_day`` are excluded
    so historical leaderboards are stable even if more scores arrive
    later (which happens in practice — people forget to share).

    Overall mode (``game is None``) uses the same renderer as the
    daily recap's "Week so far" block so the format stays consistent
    across surfaces. Game-filtered mode shows per-player cumulative
    time + submissions for that single game.

    No-peek gate: when ``target_day`` is today, the requester must
    have submitted every enabled game today before the leaderboard
    unlocks. Past-day leaderboards are never gated.
    """
    if settings is None:
        return "Leaderboard isn't available in this context."
    from .scheduler import _weekly_leaderboard_lines

    target_day = target_day or la_date(now)
    today = la_date(now)
    if (
        target_day == today
        and from_ is not None
        and settings.enabled_games
    ):
        played = _games_played_today_by(
            repo, from_, profile_name, today, settings.enabled_games,
            group_id=group_id,
        )
        if played != set(settings.enabled_games):
            return _no_peek_leaderboard(played, settings.enabled_games)
    monday, sunday = week_bounds(target_day)
    week_scores = repo.list_scores(
        date_from=monday, date_to=sunday, group_id=group_id
    )
    filtered = [
        s for s in week_scores
        if s.game in settings.enabled_games and s.puzzle_date <= target_day
    ]
    if not filtered:
        return (
            f"No scores yet for week of "
            f"{monday.strftime('%a %d %b %Y')}."
        )

    header_date = target_day.strftime("%a %d %b %Y")

    if game is None:
        # Prior = week scores strictly before target_day, so the
        # arrows compare the requested day's board to the preceding
        # day's board (None on Monday → no arrows, by design).
        prior = [s for s in filtered if s.puzzle_date < target_day]
        from .jobs import absent_player_names_for_week
        from .scheduler import _game_winners_lines, _prize_lines
        from .scoring import prize_allocations, weekly_leaderboard as _score_weekly_lb
        lines = list(_weekly_leaderboard_lines(
            filtered,
            title=f"Week so far — {header_date}",
            prior_scores=prior,
            absent_player_names=absent_player_names_for_week(
                repo, target_day, filtered, group_id=group_id
            ),
        ))
        winners = _game_winners_lines(filtered)
        if winners:
            lines += [""] + list(winners)
        pl = _prize_lines(prize_allocations(_score_weekly_lb(filtered)))
        if pl:
            lines += [""] + list(pl)
        return "\n".join(lines)

    rendered = _render_per_game_leaderboard(
        filtered, game, f"{GAME_DISPLAY[game]} — week so far ({header_date}):"
    )
    if rendered is None:
        return f"No {GAME_DISPLAY[game]} scores yet for week of {monday.strftime('%a %d %b %Y')}."
    return rendered


# ---------------------------------------------------------------------------
# Global (cross-group) helpers and handlers
# ---------------------------------------------------------------------------


def _collect_global_scores(
    repo: Repository,
    *,
    date_from: "date",
    date_to: "date",
    grps: Optional[List] = None,
    game: Optional[str] = None,
) -> List[ScoreRow]:
    """Return all scores across every group for the given date range.

    Deduplicates by ``(player_id, game, puzzle_no)`` so a player who
    switched groups mid-period isn't counted twice. Pass ``game`` to
    restrict to a single game (avoids the PostgREST 1000-row cap).
    """
    if grps is None:
        grps = repo.list_groups()
    all_scores: List[ScoreRow] = []
    seen: set = set()
    for grp in grps:
        for s in repo.list_scores(date_from=date_from, date_to=date_to, group_id=grp.id, game=game):
            key = (s.player_id, s.game, s.puzzle_no)
            if key not in seen:
                seen.add(key)
                all_scores.append(s)
    return all_scores


def _common_games(
    grps: List,
    settings: Optional["Settings"],
) -> frozenset:
    """Intersection of enabled_games across all groups.

    Groups without an override contribute ``settings.enabled_games``.
    Falls back to the global settings when there are no groups.
    """
    fallback = settings.enabled_games if settings else frozenset(GAMES)
    if not grps:
        return fallback
    return frozenset.intersection(*(
        (grp.enabled_games if grp.enabled_games is not None else fallback)
        for grp in grps
    ))


def _handle_narrative(
    repo: "Repository",
    settings: Optional["Settings"],
    now: "datetime",
    *,
    group_id: int,
) -> str:
    """Render the narrative wrap paragraph for the current week."""
    from .narrative import build_narrative_context, render_narrative
    from .puzzles import week_bounds, la_date

    if settings is None:
        return "Narrative wrap isn't available in this context."

    today = la_date(now)
    monday, sunday = week_bounds(today)
    week_scores = repo.list_scores(date_from=monday, date_to=sunday, group_id=group_id)
    prior_mon = monday - timedelta(days=7)
    prior_scores = repo.list_scores(date_from=prior_mon, date_to=monday - timedelta(days=1), group_id=group_id)

    ctx = build_narrative_context(week_scores, prior_scores, settings.enabled_games)
    if ctx is None:
        return "Not enough data for a narrative this week — need at least 2 players."
    return render_narrative(ctx)


def _handle_global_recap(
    repo: Repository,
    settings: Optional["Settings"],
    now: "datetime",
    target_day: Optional["date"] = None,
    *,
    group_id: int,
) -> str:
    """Daily (or weekly-wrap) recap aggregated across every group.

    ``target_day`` defaults to yesterday's LA date. Deduplicates
    scores so cross-group players aren't double-counted. Uses the
    intersection of all groups' enabled games.
    """
    if settings is None:
        return "Global recap isn't available in this context."
    from .puzzles import is_last_day_of_month, is_last_day_of_year, month_bounds, year_bounds
    from .scheduler import daily_recap, weekly_wrap

    today = la_date(now)
    if target_day is None:
        target_day = today - timedelta(days=1)

    monday, sunday = week_bounds(target_day)
    grps = repo.list_groups()
    common = _common_games(grps, settings)

    week_scores = [
        s for s in _collect_global_scores(repo, date_from=monday, date_to=sunday, grps=grps)
        if s.game in common
    ]
    if not week_scores:
        return (
            f"No global scores for week of {monday.strftime('%a %d %b %Y')}."
        )

    month_scores = None
    if is_last_day_of_month(target_day):
        m_start, m_end = month_bounds(target_day)
        month_scores = [
            s for s in _collect_global_scores(repo, date_from=m_start, date_to=m_end, grps=grps)
            if s.game in common
        ]

    year_scores = None
    if is_last_day_of_year(target_day):
        y_start, y_end = year_bounds(target_day)
        year_scores = [
            s for s in _collect_global_scores(repo, date_from=y_start, date_to=y_end, grps=grps)
            if s.game in common
        ]

    day_is_complete = target_day < today
    if target_day.weekday() == 6:  # Sunday → weekly wrap
        return weekly_wrap(
            monday, sunday, week_scores,
            enabled_games=frozenset(common),
            month_scores=month_scores,
            year_scores=year_scores,
            absent_player_names=[],
            day_is_complete=day_is_complete,
        )
    return daily_recap(
        target_day, week_scores,
        enabled_games=frozenset(common),
        month_scores=month_scores,
        year_scores=year_scores,
        include_missing_today_nag=False,
        absent_player_names=[],
        day_is_complete=day_is_complete,
    )


def _handle_global_wrap(
    repo: Repository,
    settings: Optional["Settings"],
    now: "datetime",
    *,
    group_id: int,
) -> str:
    """Weekly-wrap format aggregated across every group for the current week."""
    if settings is None:
        return "Global wrap isn't available in this context."
    from .scheduler import weekly_wrap

    today = la_date(now)
    monday, sunday = week_bounds(today)
    grps = repo.list_groups()
    common = _common_games(grps, settings)

    week_scores = [
        s for s in _collect_global_scores(repo, date_from=monday, date_to=sunday, grps=grps)
        if s.game in common and s.puzzle_date <= today
    ]
    if not week_scores:
        return f"No global scores yet for week of {monday.strftime('%a %d %b %Y')}."

    return weekly_wrap(
        monday, sunday, week_scores,
        enabled_games=frozenset(common),
        absent_player_names=[],
    )


def _handle_global_times(
    repo: Repository,
    settings: Optional["Settings"],
    now: "datetime",
    *,
    group_id: int,
) -> str:
    """Per-game time-standings aggregated across all groups for the current week."""
    if settings is None:
        return "Global times aren't available in this context."
    from .scoring import _NON_TIME_GAMES
    from .scheduler import _format_seconds

    today = la_date(now)
    monday, sunday = week_bounds(today)
    grps = repo.list_groups()
    common = _common_games(grps, settings)

    all_scores = [
        s for s in _collect_global_scores(repo, date_from=monday, date_to=sunday, grps=grps)
        if s.game in common and s.puzzle_date <= today
    ]
    if not all_scores:
        return f"No global scores yet for week of {monday.strftime('%a %d %b %Y')}."

    per_game: Dict[str, Dict[int, List[int]]] = {}
    player_names: Dict[int, str] = {}
    for s in all_scores:
        if s.game in _NON_TIME_GAMES:
            continue
        bucket = per_game.setdefault(s.game, {})
        acc = bucket.setdefault(s.player_id, [0, 0])
        acc[0] += s.raw_score
        acc[1] += 1
        player_names[s.player_id] = s.player_name

    if not per_game:
        return f"No time-based global scores yet for week of {monday.strftime('%a %d %b %Y')}."

    header_date = today.strftime("%a %d %b %Y")
    lines: List[str] = [f"Global game times — week so far ({header_date}):"]
    for game in GAME_DISPLAY_ORDER:
        stats = per_game.get(game)
        if not stats:
            continue
        lines.append("")
        lines.append(f"{GAME_DISPLAY[game]}:")
        ranked = sorted(stats.items(), key=lambda kv: (kv[1][0], kv[0]))
        for i, (pid, (total, subs)) in enumerate(ranked, start=1):
            lines.append(
                f"  {i}. {player_names[pid]}: {_format_seconds(total)} (G:{subs})"
            )
    return "\n".join(lines)


def _handle_global_leaderboard(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    *,
    games: Optional[List[str]] = None,
    from_: Optional[str] = None,
    profile_name: str = "",
    group_id: int,
) -> str:
    """Weekly leaderboard aggregated across every group.

    ``games`` optionally restricts to a subset of game keys — passing
    ``['zip', 'tango']`` shows only those two games' combined standings.

    Deduplicates by ``(player_id, game, puzzle_no)`` so a player who
    switched groups mid-week isn't double-counted. No-peek gate mirrors
    the per-group leaderboard: the requester must have submitted all
    enabled games today before today's global standings unlock.
    """
    if settings is None:
        return "Global leaderboard isn't available in this context."
    from .scheduler import _game_winners_lines, _prize_lines, _weekly_leaderboard_lines
    from .scoring import prize_allocations, weekly_leaderboard as _score_weekly_lb

    today = la_date(now)
    grps = repo.list_groups()

    # Effective games for the global view: intersection of every group's
    # tracked games. Groups with no override use settings.enabled_games.
    group_game_sets = [
        (grp.enabled_games if grp.enabled_games is not None else settings.enabled_games)
        for grp in grps
    ]
    if group_game_sets:
        common_games = frozenset.intersection(*group_game_sets)
    else:
        common_games = settings.enabled_games

    if from_ is not None and common_games:
        played = _games_played_today_by(
            repo, from_, profile_name, today, common_games,
            group_id=group_id,
        )
        if played != set(common_games):
            return _no_peek_leaderboard(played, common_games)

    monday, sunday = week_bounds(today)
    all_scores: List[ScoreRow] = []
    seen: set = set()
    for grp in grps:
        for s in repo.list_scores(date_from=monday, date_to=sunday, group_id=grp.id):
            key = (s.player_id, s.game, s.puzzle_no)
            if key not in seen:
                seen.add(key)
                all_scores.append(s)

    filtered = [
        s for s in all_scores
        if s.game in common_games
        and s.puzzle_date <= today
        and (games is None or s.game in games)
    ]
    if not filtered:
        scope = (
            f"({', '.join(GAME_DISPLAY[g] for g in games)}) " if games else ""
        )
        return (
            f"No {scope}scores yet for week of "
            f"{monday.strftime('%a %d %b %Y')} across any group."
        )

    header_date = today.strftime("%a %d %b %Y")
    if games:
        game_labels = " · ".join(GAME_DISPLAY[g] for g in games)
        title = f"Global ({game_labels}) — week so far ({header_date})"
    elif frozenset(common_games) != frozenset(settings.enabled_games):
        # Show which games are in the intersection when groups differ
        common_labels = " · ".join(
            GAME_DISPLAY[g] for g in GAME_DISPLAY_ORDER if g in common_games
        )
        title = f"Global ({common_labels}) — week so far ({header_date})"
    else:
        title = f"Global — week so far ({header_date})"
    prior = [s for s in filtered if s.puzzle_date < today]
    lines = list(_weekly_leaderboard_lines(
        filtered,
        title=title,
        prior_scores=prior,
        absent_player_names=[],
    ))
    winners = _game_winners_lines(filtered)
    if winners:
        lines += [""] + list(winners)
    pl = _prize_lines(prize_allocations(_score_weekly_lb(filtered)))
    if pl:
        lines += [""] + list(pl)
    return "\n".join(lines)


def _handle_times(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    target_day: Optional[date] = None,
    *,
    group_id: int,
) -> str:
    """Per-game time-standings for the current LA week.

    Ranks players by cumulative seconds in each time-based enabled
    game, ascending (fastest first). Pinpoint is skipped since its
    raw_score is a guess count, not seconds.
    """
    if settings is None:
        return "Time standings aren't available in this context."
    from .scoring import _NON_TIME_GAMES
    from .scheduler import _format_seconds

    target_day = target_day or la_date(now)
    monday, sunday = week_bounds(target_day)
    week_scores = repo.list_scores(
        date_from=monday, date_to=sunday, group_id=group_id
    )
    filtered = [
        s for s in week_scores
        if s.game in settings.enabled_games and s.puzzle_date <= target_day
    ]
    if not filtered:
        return f"No scores yet for week of {monday.strftime('%a %d %b %Y')}."

    # per-game per-player: (total_seconds, submissions)
    per_game: Dict[str, Dict[int, List[int]]] = {}
    player_names: Dict[int, str] = {}
    for s in filtered:
        if s.game in _NON_TIME_GAMES:
            continue
        bucket = per_game.setdefault(s.game, {})
        acc = bucket.setdefault(s.player_id, [0, 0])
        acc[0] += s.raw_score
        acc[1] += 1
        player_names[s.player_id] = s.player_name

    if not per_game:
        return f"No time-based scores yet for week of {monday.strftime('%a %d %b %Y')}."

    header_date = target_day.strftime("%a %d %b %Y")
    lines: List[str] = [f"Game times — week so far ({header_date}):"]
    for game in GAME_DISPLAY_ORDER:
        stats = per_game.get(game)
        if not stats:
            continue
        lines.append("")
        lines.append(f"{GAME_DISPLAY[game]}:")
        # Sort by total time ascending (fastest first), tiebreak by pid.
        ranked = sorted(
            stats.items(),
            key=lambda kv: (kv[1][0], kv[0]),
        )
        for i, (pid, (total, subs)) in enumerate(ranked, start=1):
            lines.append(
                f"  {i}. {player_names[pid]}: "
                f"{_format_seconds(total)} (G:{subs})"
            )
    return "\n".join(lines)


def _handle_period_leaderboard(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    *,
    period: str,
    game: Optional[str] = None,
    group_id: int,
) -> str:
    """Shared renderer for ``month`` / ``year`` commands.

    ``period`` is ``"month"`` or ``"year"``. Aggregates every score
    in the current LA month / year up through today and renders the
    full summary (leaderboard + game winners + prizes) when ``game``
    is ``None``, or a per-game leaderboard when ``game`` is provided.

    Scope goes up to today only — future days obviously have no
    scores, so the "to date" framing is implicit.
    """
    if settings is None:
        return f"{period.title()}-to-date isn't available in this context."
    from .scheduler import period_summary

    today = la_date(now)
    if period == "month":
        start, _end = month_bounds(today)
        period_label = today.strftime("%b %Y")
    else:
        start, _end = year_bounds(today)
        period_label = str(today.year)

    scores = repo.list_scores(
        date_from=start, date_to=today, group_id=group_id
    )
    filtered = [
        s for s in scores
        if s.game in settings.enabled_games and s.puzzle_date <= today
    ]
    if not filtered:
        if game is None:
            return f"No scores yet this {period}."
        return f"No {GAME_DISPLAY[game]} scores yet this {period}."

    if game is not None:
        title = f"{GAME_DISPLAY[game]} — {period} so far ({period_label}):"
        rendered = _render_per_game_leaderboard(filtered, game, title)
        if rendered is None:
            return f"No {GAME_DISPLAY[game]} scores yet this {period}."
        return rendered

    # Full summary: leaderboard + game winners + prizes.
    lines = period_summary(
        f"{period.title()} so far — {period_label}",
        filtered,
        settings.enabled_games,
    )
    return "\n".join(lines)


def _handle_missing(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    *,
    group_id: int,
) -> str:
    """List players who submitted earlier this week but skipped today.

    Same logic the daily recap's passive-aggressive nudge uses, but
    surfaced on demand so a player can check who's still to play
    without waiting for the 5pm recap.
    """
    if settings is None:
        return "Missing-today check isn't available in this context."
    filtered, today, _, _ = _week_filtered_scores(
        repo, settings, now, group_id=group_id
    )
    if not filtered:
        return "Nobody's played yet this week."

    today_ids = {s.player_id for s in filtered if s.puzzle_date == today}
    missing: List[str] = []
    seen: set = set()
    for s in filtered:
        if s.puzzle_date >= today or s.player_id in today_ids or s.player_id in seen:
            continue
        seen.add(s.player_id)
        missing.append(s.player_name)

    if not missing:
        return "Nobody missing today — full turnout."
    return "Still to play today:\n  " + "\n  ".join(f"- {n}" for n in missing)


def _handle_pb(
    repo: Repository, from_: str, profile_name: str, *, group_id: int
) -> str:
    """Just the personal-bests block from ``stats`` — shorter and
    more scanable when a player only cares about their PBs."""
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    scores = repo.list_player_scores(player.id, group_id=group_id)
    if not scores:
        return "No scores recorded yet — submit a share to set a personal best!"

    best: Dict[str, int] = {}
    count: Dict[str, int] = {}
    for s in scores:
        count[s.game] = count.get(s.game, 0) + 1
        if s.game not in best or s.raw_score < best[s.game]:
            best[s.game] = s.raw_score

    lines = [f"Personal bests for {player.display_name}:"]
    for game in GAME_DISPLAY_ORDER:
        if game in best:
            lines.append(
                f"  {GAME_DISPLAY[game]}: "
                f"{format_raw_score(game, best[game])} ({count[game]} subs)"
            )
    return "\n".join(lines)


def _handle_games(
    settings: Optional[Settings],
    group: Optional[Group] = None,
) -> str:
    """Show which games count toward the leaderboard vs which are
    parsed-but-untracked. When the group has its own game list, shows
    the override and the global default side by side."""
    if settings is None:
        return "Game list isn't available in this context."
    # Use group override if present, else global settings
    effective = (
        group.enabled_games if (group is not None and group.enabled_games is not None)
        else settings.enabled_games
    )
    enabled_display = [
        GAME_DISPLAY[g] for g in GAME_DISPLAY_ORDER if g in effective
    ]
    disabled_display = [
        GAME_DISPLAY[g] for g in GAME_DISPLAY_ORDER if g not in effective
    ]
    header = "Tracked games (this group):" if (
        group is not None and group.enabled_games is not None
    ) else f"Tracked games ({len(enabled_display)}):"
    lines = [header]
    for g in enabled_display:
        lines.append(f"  - {g}")
    if disabled_display:
        lines.append("")
        lines.append("Not tracked (scores still stored, not scored):")
        for g in disabled_display:
            lines.append(f"  - {g}")
    if group is not None and group.enabled_games is not None:
        global_display = [
            GAME_DISPLAY[g] for g in GAME_DISPLAY_ORDER if g in settings.enabled_games
        ]
        if frozenset(effective) != frozenset(settings.enabled_games):
            lines.append(f"\nGlobal default: {', '.join(global_display)}")
    return "\n".join(lines)


def _handle_track(
    repo: Repository,
    settings: Optional[Settings],
    group: Group,
    raw_words: str,
) -> str:
    """Set this group's tracked games.

    ``track queens zip tango`` — set exact game list for this group.
    ``track reset`` / ``track default`` — remove override, use global setting.
    ``track all`` — track every known game.
    """
    words = raw_words.strip().lower().split()
    if not words:
        return (
            "Usage: track <game1> <game2> ...\n"
            "E.g. track queens zip tango\n"
            "Or: track reset (use global default)"
        )
    if words[0] in ("reset", "default", "off"):
        repo.set_group_games(group.id, None)
        fallback = (
            ", ".join(GAME_DISPLAY[g] for g in GAME_DISPLAY_ORDER
                      if settings and g in settings.enabled_games)
            if settings else "global default"
        )
        return f"Game tracking reset to global default: {fallback}"

    if words[0] == "all":
        repo.set_group_games(group.id, frozenset(GAMES))
        names = ", ".join(GAME_DISPLAY[g] for g in GAME_DISPLAY_ORDER if g in GAMES)
        return f"Now tracking all games for this group: {names}"

    resolved: List[str] = []
    unknown: List[str] = []
    for w in words:
        key = _parse_single_game_key(w)
        if key is not None:
            resolved.append(key)
        else:
            unknown.append(w)
    if unknown:
        return (
            f"Unknown game(s): {', '.join(unknown)}. "
            f"Known games: {', '.join(GAME_DISPLAY[g] for g in GAME_DISPLAY_ORDER)}"
        )
    repo.set_group_games(group.id, frozenset(resolved))
    names = ", ".join(GAME_DISPLAY[g] for g in GAME_DISPLAY_ORDER if g in resolved)
    return f"Now tracking for this group: {names}"


# Static scoring explanation. Stable text — kept inline so it's easy
# to tweak when the rules change without hunting through templates.
_RULES_TEXT = (
    "Scoring — short version:\n"
    "\n"
    "3–5 player time-based rounds (no ties) use competitive scoring:\n"
    "  * Base points by rank: 5/4/3/2/1.\n"
    "  * Tight game (times within 50% of fastest) — base points stand.\n"
    "  * Clear winner (2nd is >1.3x slower than 1st) — 1st gets a\n"
    "    bonus up to +2, debited proportionally from others.\n"
    "  * Front cluster (top K tight + drop >1.3x to next player) —\n"
    "    top K share a bonus pool weighted by 1/time. K = 4/3/2,\n"
    "    largest cluster that fits wins. Pool scales with the drop:\n"
    "    0.5 pts at 1.3x, 1.5 pts at 1.6x, 2.0 pts at 2.0x+.\n"
    "  * Floor 0.5; total preserved; rounded to 1 d.p.\n"
    "\n"
    "Everything else (pinpoint, ties, 6+ players) uses legacy ranks:\n"
    "  1st=5, 2nd=4, 3rd=3, 4th=2, 5th=1. Tied players share the\n"
    "  ceiling of the average.\n"
    "\n"
    "Weekly prizes: Most firsts, Most lasts, Best average (≥5 subs),\n"
    "Fastest average time (≥10 time-based rounds)."
)


def _handle_rules() -> str:
    return _RULES_TEXT


def _handle_prizes(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    *,
    group_id: int,
) -> str:
    """Live prize snapshot for the in-progress week.

    Uses the same computation as the weekly wrap but returns only the
    prizes section — useful for a "where do I stand on the prizes"
    check without pulling the full wrap formatter.
    """
    if settings is None:
        return "Prize snapshot isn't available in this context."
    from .scheduler import _format_seconds
    from .scoring import prize_allocations, weekly_leaderboard

    filtered, _, _, _ = _week_filtered_scores(
        repo, settings, now, group_id=group_id
    )
    if not filtered:
        return "No scores yet this week — prizes unawarded."
    lb = weekly_leaderboard(filtered)
    prizes = prize_allocations(lb)

    lines = ["Prizes so far:"]
    if prizes.most_firsts is not None:
        firsts = prizes.most_firsts.first_places
        word = "1 first" if firsts == 1 else f"{firsts} firsts"
        lines.append(f"  Most firsts: {prizes.most_firsts.player_name} ({word})")
    if prizes.most_lasts is not None:
        lasts = prizes.most_lasts.last_places
        word = "1 last" if lasts == 1 else f"{lasts} lasts"
        lines.append(f"  Most lasts: {prizes.most_lasts.player_name} ({word})")
    if prizes.best_average is not None:
        ba = prizes.best_average
        lines.append(
            f"  Best average: {ba.player_name} "
            f"(avg {ba.average_points:.1f}, {ba.submissions} subs)"
        )
    if prizes.fastest_average_time is not None:
        ft = prizes.fastest_average_time
        lines.append(
            f"  Fastest average time: {ft.player_name} "
            f"({_format_seconds(round(ft.average_time))}/round "
            f"over {ft.time_based_submissions} rounds)"
        )
    if len(lines) == 1:
        lines.append("  Nobody has qualified yet.")
    return "\n".join(lines)


def _handle_streak(
    repo: Repository,
    from_: str,
    profile_name: str,
    now: datetime,
    *,
    group_id: int,
) -> str:
    """Sender's current consecutive-days submission streak.

    "Current" means the most recent unbroken run ending today or
    yesterday (so a player doesn't lose their streak mid-morning just
    because they haven't played yet). Days are counted in LA time to
    match the puzzle-rollover convention.
    """
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    scores = repo.list_player_scores(player.id, group_id=group_id)
    if not scores:
        return f"{player.display_name}: no streak yet — submit a score to start one."

    played_days = {s.puzzle_date for s in scores}
    today = la_date(now)
    # If the player hasn't played today yet, don't zero them out — only
    # break the streak once a whole day passes unplayed. Start counting
    # from today (if played) else yesterday.
    cursor = today if today in played_days else today - timedelta(days=1)
    streak = 0
    while cursor in played_days:
        streak += 1
        cursor -= timedelta(days=1)

    if streak == 0:
        last = max(played_days)
        return (
            f"{player.display_name}: 0-day streak — "
            f"last played {last.strftime('%a %d %b')}."
        )
    day_word = "day" if streak == 1 else "days"
    return f"{player.display_name}: {streak}-{day_word} streak 🔥"


def _parse_vs_command(lower: str) -> Optional[str]:
    """Extract the opponent name from ``vs <name>`` / ``versus <name>``.
    Returns the raw (lower-cased) name or ``None`` when not a vs command."""
    m = re.match(r"^(?:vs|versus)\s+(.+)$", lower)
    if not m:
        return None
    return m.group(1).strip()


def _handle_vs(
    repo: Repository,
    from_: str,
    profile_name: str,
    opponent_name_lower: str,
    *,
    group_id: int,
) -> str:
    """All-time head-to-head between the sender and a named opponent.

    Matches opponent by display name (case-insensitive). Counts only
    rounds both players submitted (same ``game`` + ``puzzle_no``).
    Per-game wins/losses/ties broken out plus an overall total.
    """
    display_name = (profile_name or "").strip() or from_
    me = repo.get_or_create_player(from_, display_name)
    my_scores = repo.list_player_scores(me.id, group_id=group_id)
    if not my_scores:
        return "You haven't submitted any scores yet — nothing to compare."

    # Walk every ScoreRow in the week-wide list_scores window to find
    # the opponent by name. We use a wide date window since this is
    # all-time comparison.
    # There isn't a "find player by name" method, so scan all-time by
    # iterating scores back to epoch — pragmatic given group size.
    all_scores = repo.list_scores(
        date_from=date(2000, 1, 1),
        date_to=date(2100, 1, 1),
        group_id=group_id,
    )
    opp_candidates = {
        s.player_id: s.player_name
        for s in all_scores
        if s.player_name.lower() == opponent_name_lower
    }
    if not opp_candidates:
        return (
            f"Couldn't find a player named '{opponent_name_lower}'. "
            "Names are matched case-insensitively; make sure they've submitted at least one score."
        )
    if me.id in opp_candidates:
        return "You can't play against yourself."
    opp_id = next(iter(opp_candidates))
    opp_name = opp_candidates[opp_id]

    # Index my scores by (game, puzzle_no) for O(1) lookups.
    my_index = {(s.game, s.puzzle_no): s for s in my_scores}

    per_game: Dict[str, List[int]] = {}  # game -> [wins, losses, ties]
    for s in all_scores:
        if s.player_id != opp_id:
            continue
        mine = my_index.get((s.game, s.puzzle_no))
        if mine is None:
            continue
        bucket = per_game.setdefault(s.game, [0, 0, 0])
        if mine.raw_score < s.raw_score:
            bucket[0] += 1  # lower is better → my win
        elif mine.raw_score > s.raw_score:
            bucket[1] += 1
        else:
            bucket[2] += 1

    if not per_game:
        return f"No shared rounds yet between you and {opp_name}."

    lines = [f"{me.display_name} vs {opp_name} (all-time):"]
    total_w = total_l = total_t = 0
    for game in GAME_DISPLAY_ORDER:
        wlt = per_game.get(game)
        if wlt is None:
            continue
        w, l, t = wlt
        total_w += w
        total_l += l
        total_t += t
        tie_part = f", {t}T" if t else ""
        lines.append(f"  {GAME_DISPLAY[game]}: {w}W–{l}L{tie_part}")
    tie_part = f", {total_t} tie{'s' if total_t != 1 else ''}" if total_t else ""
    lines.append(f"Overall: {total_w}W–{total_l}L{tie_part}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Personal analytics commands
# ---------------------------------------------------------------------------


def _parse_single_game_key(word: str) -> Optional[str]:
    """Return a game key for ``word`` (exact key or display-name match),
    or ``None`` if not recognised."""
    if word in GAMES:
        return word
    display_to_key = {GAME_DISPLAY[k].lower(): k for k in GAMES}
    return display_to_key.get(word)


def _parse_history_command(lower: str) -> Tuple[bool, Optional[str]]:
    """Parse ``history`` or ``history <game>``.

    Returns ``(is_history, game_key_or_None)``. ``game_key_or_None`` is
    ``None`` for the bare ``history`` command (show all games).
    Returns ``(False, None)`` when the text doesn't start with
    ``history``.
    """
    if lower == "history":
        return True, None
    if not lower.startswith("history "):
        return False, None
    word = lower[len("history "):].strip()
    key = _parse_single_game_key(word)
    if key is not None:
        return True, key
    return True, "__bad__"  # recognised prefix, unrecognised game


def _handle_history(
    repo: Repository,
    from_: str,
    profile_name: str,
    game_key: Optional[str],
    *,
    group_id: int,
    settings: Optional[Settings] = None,
) -> str:
    """Show personal score history — last 20 entries for a game (or
    all enabled games over the last 30 days when no game specified)."""
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    all_scores = repo.list_player_scores(player.id, group_id=group_id)

    enabled = settings.enabled_games if settings else frozenset(GAMES)

    if game_key is not None:
        # Single-game history
        rows = sorted(
            [s for s in all_scores if s.game == game_key],
            key=lambda s: s.puzzle_date,
            reverse=True,
        )[:20]
        if not rows:
            return (
                f"No {GAME_DISPLAY[game_key]} scores recorded yet. "
                "Submit a share to get started!"
            )
        pb = min(s.raw_score for s in all_scores if s.game == game_key)
        header = (
            f"{GAME_DISPLAY[game_key]} history for {display_name} "
            f"({len([s for s in all_scores if s.game == game_key])} scores):"
        )
        lines = [header]
        for s in rows:
            marker = " ✓ PB" if s.raw_score == pb else ""
            lines.append(
                f"  #{s.puzzle_no} {s.puzzle_date.strftime('%a %d %b')} "
                f"— {format_raw_score(game_key, s.raw_score)}{marker}"
            )
        return "\n".join(lines)

    # All-games summary: last 30 days, per enabled game
    cutoff = (
        max(s.puzzle_date for s in all_scores) if all_scores else None
    )
    if cutoff is None:
        return "No scores recorded yet. Submit a LinkedIn game share to get started!"
    from_date = cutoff - timedelta(days=30)
    recent = [
        s for s in all_scores
        if s.game in enabled and s.puzzle_date >= from_date
    ]
    if not recent:
        return f"No scores in the last 30 days for {display_name}."
    pb_by_game = {
        g: min(s.raw_score for s in all_scores if s.game == g)
        for g in GAMES
        if any(s.game == g for s in all_scores)
    }
    lines = [f"Recent scores for {display_name} (last 30 days):"]
    for game in GAME_DISPLAY_ORDER:
        game_rows = sorted(
            [s for s in recent if s.game == game],
            key=lambda s: s.puzzle_date,
            reverse=True,
        )
        if not game_rows:
            continue
        lines.append(f"\n{GAME_DISPLAY[game]}:")
        for s in game_rows:
            marker = " ✓ PB" if s.raw_score == pb_by_game.get(game) else ""
            lines.append(
                f"  #{s.puzzle_no} {s.puzzle_date.strftime('%a %d %b')} "
                f"— {format_raw_score(game, s.raw_score)}{marker}"
            )
    return "\n".join(lines)


def _handle_trends(
    repo: Repository,
    from_: str,
    profile_name: str,
    now: datetime,
    settings: Settings,
    *,
    group_id: int,
) -> str:
    """Compare last 4 weeks vs prior 4 weeks per game.

    Shows ↓ (improving), ↑ (declining), → (steady) for each game
    that has enough data in both halves. Time games: lower is better.
    Pinpoint: lower guesses is better.
    """
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    all_scores = repo.list_player_scores(player.id, group_id=group_id)

    if not all_scores:
        return "No scores recorded yet. Submit some shares first!"

    today = la_date(now)
    monday, _ = week_bounds(today)
    # "Last 4 weeks" = the 4 completed weeks before the current week.
    last4_end = monday - timedelta(days=1)
    last4_start = monday - timedelta(weeks=4)
    prior4_end = last4_start - timedelta(days=1)
    prior4_start = last4_start - timedelta(weeks=4)

    MIN_PLAYS = 3  # minimum plays in each period to show a trend

    enabled = settings.enabled_games
    lines = [f"Trends for {display_name} (last 4 wks vs prior 4 wks):"]
    no_data: List[str] = []

    for game in GAME_DISPLAY_ORDER:
        if game not in enabled:
            continue
        game_scores = [s for s in all_scores if s.game == game]
        last4 = [
            s.raw_score for s in game_scores
            if last4_start <= s.puzzle_date <= last4_end
        ]
        prior4 = [
            s.raw_score for s in game_scores
            if prior4_start <= s.puzzle_date <= prior4_end
        ]
        if len(last4) < MIN_PLAYS or len(prior4) < MIN_PLAYS:
            no_data.append(GAME_DISPLAY[game])
            continue

        avg_last = sum(last4) / len(last4)
        avg_prior = sum(prior4) / len(prior4)
        pct_change = (avg_last - avg_prior) / avg_prior  # negative = improved

        if abs(pct_change) < 0.05:
            arrow = "→"
            label = "steady"
        elif pct_change < 0:
            arrow = "↓"  # faster / fewer guesses
            label = "faster" if game != "pinpoint" else "fewer guesses"
        else:
            arrow = "↑"  # slower / more guesses
            label = "slower" if game != "pinpoint" else "more guesses"

        fmt_last = format_raw_score(game, int(round(avg_last)))
        fmt_prior = format_raw_score(game, int(round(avg_prior)))
        name_col = f"{GAME_DISPLAY[game]}:"
        lines.append(f"  {name_col:<14} {arrow}  {fmt_prior} → {fmt_last}  ({label})")

    if not no_data and len(lines) == 1:
        return f"Not enough history yet to show trends for {display_name}."
    if no_data:
        lines.append(f"\nNot enough data: {', '.join(no_data)}")
    return "\n".join(lines)


def _handle_best_worst_day(
    repo: Repository,
    from_: str,
    profile_name: str,
    kind: str,
    *,
    group_id: int,
) -> str:
    """Show the player's single best or worst performance day.

    Quality of a day is measured by the average percentile of each
    game's score within the player's personal history for that game
    (1.0 = PB on everything, 0.0 = worst-ever on everything).
    Requires at least 2 games on the candidate day and 2+ prior
    scores per game to compute a meaningful percentile.
    """
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    all_scores = repo.list_player_scores(player.id, group_id=group_id)

    if not all_scores:
        return "No scores recorded yet. Submit some shares first!"

    from collections import defaultdict
    by_game: Dict[str, List[int]] = defaultdict(list)
    for s in all_scores:
        by_game[s.game].append(s.raw_score)

    by_date: Dict[date, List[ScoreRow]] = defaultdict(list)
    for s in all_scores:
        by_date[s.puzzle_date].append(s)

    date_quality: Dict[date, float] = {}
    for day, day_scores in by_date.items():
        if len(day_scores) < 2:
            continue
        percentiles: List[float] = []
        for s in day_scores:
            history = sorted(by_game[s.game])  # asc = better first
            if len(history) < 2:
                continue
            rank = history.index(s.raw_score)  # 0 = best (PB)
            pct = 1.0 - rank / (len(history) - 1)  # 1.0 = PB, 0.0 = worst
            percentiles.append(pct)
        if percentiles:
            date_quality[day] = sum(percentiles) / len(percentiles)

    if not date_quality:
        return (
            f"Not enough history yet to determine your {kind} day. "
            "Keep submitting!"
        )

    if kind == "best":
        target_day = max(date_quality, key=lambda d: date_quality[d])
        title = f"Best day for {display_name}:"
    else:
        target_day = min(date_quality, key=lambda d: date_quality[d])
        title = f"Worst day for {display_name}:"

    pb_by_game = {
        g: min(raws) for g, raws in by_game.items()
    }

    day_rows = sorted(by_date[target_day], key=lambda s: GAME_DISPLAY_ORDER.index(s.game)
                      if s.game in GAME_DISPLAY_ORDER else 99)
    lines = [
        title,
        f"  {target_day.strftime('%a %d %b %Y')} ({len(day_rows)} games):",
    ]
    for s in day_rows:
        marker = " ✓ PB" if s.raw_score == pb_by_game.get(s.game) else ""
        lines.append(
            f"  {GAME_DISPLAY[s.game]:<14} — "
            f"{format_raw_score(s.game, s.raw_score)}{marker}"
        )
    return "\n".join(lines)


def _handle_pace(
    repo: Repository,
    from_: str,
    profile_name: str,
    now: datetime,
    settings: Settings,
    *,
    group_id: int,
) -> str:
    """Project the sender's weekly points total through Sunday.

    Uses the current week's submissions-per-day rate to estimate
    how many more games the player will play, then applies their
    current points-per-game rate to project a final total.
    """
    from .scoring import weekly_leaderboard
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)

    today = la_date(now)
    monday, sunday = week_bounds(today)
    week_scores = repo.list_scores(
        date_from=monday, date_to=today, group_id=group_id
    )
    filtered = [s for s in week_scores if s.game in settings.enabled_games]

    lb = weekly_leaderboard(filtered)
    if not lb:
        return "No scores this week yet — nothing to project from."

    # Find player in leaderboard
    my_entry = next((e for e in lb if e.player_id == player.id), None)
    if my_entry is None:
        return (
            f"{display_name} hasn't submitted anything this week yet — "
            "play some games first!"
        )

    current_pts = my_entry.total_points
    submissions = my_entry.submissions
    rank = next(i + 1 for i, e in enumerate(lb) if e.player_id == player.id)

    days_elapsed = max(1, (today - monday).days + 1)
    days_remaining = (sunday - today).days
    games_per_day = submissions / days_elapsed
    pts_per_game = current_pts / submissions if submissions else 0.0
    projected_extra = pts_per_game * games_per_day * days_remaining
    projected_lo = round(current_pts + projected_extra * 0.8, 1)
    projected_hi = round(current_pts + projected_extra * 1.2, 1)

    from .jobs import _fmt_weekly_points
    lines = [
        f"Pace for {display_name} — week {today.isocalendar()[1]}:",
        (
            f"  Current:   {_fmt_weekly_points(current_pts)} "
            f"(rank {rank} of {len(lb)}, {submissions} games played)"
        ),
    ]
    if days_remaining == 0:
        lines.append("  Week complete — no projection needed.")
    else:
        lines.append(
            f"  Projected: ~{_fmt_weekly_points(projected_lo)}–"
            f"{_fmt_weekly_points(projected_hi)} by Sunday"
        )

    # Gap to nearest neighbours
    if rank > 1:
        above = lb[rank - 2]
        gap_above = above.total_points - current_pts
        lines.append(
            f"  Gap to {rank - 1}{_ordinal_suffix(rank - 1)} place: "
            f"{_fmt_weekly_points(gap_above)} behind"
        )
    if rank < len(lb):
        below = lb[rank]
        gap_below = current_pts - below.total_points
        lines.append(
            f"  Ahead of {rank + 1}{_ordinal_suffix(rank + 1)} place: "
            f"{_fmt_weekly_points(gap_below)}"
        )

    return "\n".join(lines)


def _ordinal_suffix(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


_DOW_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_DOW_FULL = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

def _handle_estimate(
    repo: "Repository",
    settings: Optional["Settings"],
    from_: str,
    profile_name: str,
    now: "datetime",
    *,
    group_id: int,
) -> str:
    """Show the raw score a player needs in each unplayed game today to
    overtake the current weekly leader."""
    from .scoring import estimate_score_to_beat, weekly_leaderboard
    from .puzzles import week_bounds, la_date
    from .parsers import format_raw_score as _fmt_raw

    today = la_date(now)
    monday, sunday = week_bounds(today)
    enabled_games: FrozenSet[str] = settings.enabled_games if settings else frozenset()

    player = repo.get_or_create_player(from_, profile_name)
    week_scores = repo.list_scores(date_from=monday, date_to=sunday, group_id=group_id)
    week_filtered = [s for s in week_scores if s.game in enabled_games]
    lb = weekly_leaderboard(week_filtered)

    if not lb:
        return "No scores this week yet — nothing to estimate from."

    my_entry = next((e for e in lb if e.player_id == player.id), None)
    my_pts = my_entry.total_points if my_entry else 0.0
    my_rank = lb.index(my_entry) + 1 if my_entry else len(lb) + 1
    leader = lb[0]

    if my_rank == 1 and len(lb) > 1:
        gap_to_2nd = round(leader.total_points - lb[1].total_points, 1)
        header = (
            f"You're leading, {profile_name} — {_fmt_pts(leader.total_points)} pts "
            f"(+{_fmt_pts(gap_to_2nd)} ahead of {lb[1].player_name})."
        )
    elif my_rank == 1:
        return f"You're the only one on the board this week, {profile_name}. Play on!"
    else:
        header = (
            f"To overtake {leader.player_name} "
            f"({_fmt_pts(leader.total_points)} pts) you need:"
        )

    today_scores = [s for s in week_scores if s.puzzle_date == today]
    played_today = {s.game for s in today_scores if s.player_id == player.id}
    unplayed_time = [g for g in enabled_games if g not in played_today and g != "pinpoint"]
    pinpoint_unplayed = "pinpoint" in enabled_games and "pinpoint" not in played_today

    if not unplayed_time and not pinpoint_unplayed:
        if my_rank > 1:
            pts_gap = round(leader.total_points - my_pts, 1)
            return f"{header}\nYou've played everything today. Gap: {_fmt_pts(pts_gap)} pts."
        return header

    lines = [header]
    target = leader.total_points - my_pts + 0.1

    for game in sorted(unplayed_time):
        competitors = [s.raw_score for s in today_scores if s.game == game]
        needed = estimate_score_to_beat(target, competitors, game)
        game_label = GAME_DISPLAY.get(game, game)
        if needed is None:
            lines.append(f"  {game_label}: not achievable at current pace")
        else:
            lines.append(f"  {game_label}: sub {_fmt_raw(game, needed)}")

    if pinpoint_unplayed:
        lines.append(f"  {GAME_DISPLAY.get('pinpoint', 'Pinpoint')}: (guess-based — can't estimate)")

    lines.append("(based on scores submitted so far today)")
    return "\n".join(lines)


_BY_DAY_BARE = frozenset({"by day", "byday", "day stats", "daystats", "days"})
_BY_DAY_PREFIX = ("by day ", "byday ", "day stats ", "daystats ", "days ")


def _parse_by_day_command(
    lower: str,
) -> Optional[Tuple[Optional[str], Optional[int]]]:
    """Parse ``by day [game] [n]`` variants.

    Accepts tokens in any order: game name and integer limit are
    distinguished by type. Returns ``(game_key_or_None, limit_or_None)``
    on a match, ``None`` if the input isn't a by-day command at all.

    Examples:
      ``by day``        → (None, None)
      ``by day 20``     → (None, 20)
      ``by day zip``    → ('zip', None)
      ``by day zip 20`` → ('zip', 20)
      ``by day 20 zip`` → ('zip', 20)
    """
    if lower in _BY_DAY_BARE:
        return None, None
    rest: Optional[str] = None
    for prefix in _BY_DAY_PREFIX:
        if lower.startswith(prefix):
            rest = lower[len(prefix):].strip()
            break
    if rest is None:
        return None  # not a by-day command

    game_key: Optional[str] = None
    limit: Optional[int] = None
    for part in rest.split():
        if part.isdigit() and limit is None:
            limit = int(part)
        else:
            key = _parse_single_game_key(part)
            if key is not None and game_key is None:
                game_key = key
    return game_key, limit


def _handle_dow_stats(
    repo: Repository,
    from_: str,
    profile_name: str,
    game_filter: Optional[str],  # None = all games; else specific game key
    *,
    group_id: int,
    settings: Optional[Settings] = None,
    limit: Optional[int] = None,  # restrict to last N scores per game
) -> str:
    """Per-day-of-week breakdown for each game.

    For every (game, weekday) pair with ≥ 2 plays, shows:
      - play count
      - average score
      - personal best (day's best)
      - personal worst (day's worst)
      - quartile label: the average falls in Q1 (top 25%), Q2, Q3, or
        Q4 (bottom 25%) relative to ALL of the player's scores for
        that game.

    ``limit`` restricts analysis to the last N scores per game (sorted
    by puzzle_date descending). The quartile boundaries are computed on
    that same window, not the full history.

    Requires ≥ 4 total scores per game to compute a meaningful quartile.
    """
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    all_scores = repo.list_player_scores(player.id, group_id=group_id)

    if not all_scores:
        return "No scores recorded yet. Submit some shares first!"

    enabled = settings.enabled_games if settings else frozenset(GAMES)

    games_to_show: List[str] = []
    if game_filter is not None:
        if game_filter in enabled:
            games_to_show = [game_filter]
        else:
            return f"{GAME_DISPLAY[game_filter]} isn't in the enabled games list."
    else:
        games_to_show = [g for g in GAME_DISPLAY_ORDER if g in enabled]

    limit_label = f" — last {limit}" if limit else ""
    header = f"Day breakdown for {display_name}{limit_label}:"
    sections: List[str] = [header]
    no_data: List[str] = []

    for game in games_to_show:
        # Sort newest-first so limit keeps the most recent scores.
        game_scores_all = sorted(
            [s for s in all_scores if s.game == game],
            key=lambda s: s.puzzle_date, reverse=True,
        )
        if not game_scores_all:
            continue
        game_scores = game_scores_all[:limit] if limit else game_scores_all

        total_count = len(game_scores)
        all_raws = sorted(s.raw_score for s in game_scores)

        # Group by weekday (0=Mon … 6=Sun)
        by_dow: Dict[int, List[int]] = {}
        for s in game_scores:
            dow = s.puzzle_date.weekday()
            by_dow.setdefault(dow, []).append(s.raw_score)

        # Quartile boundaries on the full distribution (lower = better).
        # Q1 = top 25% (best), Q4 = bottom 25% (worst).
        def _quartile_label(avg_raw: float) -> str:
            if total_count < 4:
                return ""
            q1_cut = all_raws[total_count // 4]          # 25th pct = top quarter
            q3_cut = all_raws[int(total_count * 0.75)]   # 75th pct = bottom quarter
            if avg_raw <= q1_cut:
                return " ✦ top quarter"
            if avg_raw >= q3_cut:
                return " ✧ bottom quarter"
            return ""

        rows: List[Tuple[int, List[int]]] = sorted(by_dow.items())
        # Skip games where no weekday has ≥ 2 plays
        if not any(len(v) >= 2 for _, v in rows):
            no_data.append(GAME_DISPLAY[game])
            continue

        total_all = len(game_scores_all)
        count_label = (
            f"{total_count} of {total_all}"
            if (limit and total_count < total_all)
            else str(total_count)
        )
        name_col = f"{GAME_DISPLAY[game]} ({count_label} plays):"
        sections.append(f"\n{name_col}")

        for dow, raws in rows:
            if len(raws) < 2:
                # Single-play days: show raw data but no quartile
                fmt = format_raw_score(game, raws[0])
                sections.append(
                    f"  {_DOW_NAMES[dow]}  1 play   {fmt}"
                )
                continue
            avg = sum(raws) / len(raws)
            best = min(raws)
            worst = max(raws)
            ql = _quartile_label(avg)
            sections.append(
                f"  {_DOW_NAMES[dow]}  "
                f"{len(raws)} plays  "
                f"avg {format_raw_score(game, int(round(avg)))}  "
                f"best {format_raw_score(game, best)}  "
                f"worst {format_raw_score(game, worst)}"
                f"{ql}"
            )

    if len(sections) == 1:
        return (
            f"Not enough data yet to show a day breakdown for {display_name}. "
            "Keep submitting!"
        )
    if no_data:
        sections.append(f"\nNot enough data (need ≥ 2 plays per day): {', '.join(no_data)}")
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# records / dow — per-game best raw-score commands
# ---------------------------------------------------------------------------


_DOW_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Prompt shown when the user runs ``records`` or ``dow`` without a game name.
def _records_game_list(enabled: "FrozenSet[str]") -> str:
    names = "  " + "\n  ".join(
        GAME_DISPLAY[g] for g in GAME_DISPLAY_ORDER if g in enabled
    )
    return (
        "Specify a game:\n"
        "  records <game>     — all-time top scores (e.g. records queens)\n"
        "  dow <game>         — best score per day of week this year\n"
        "\nAvailable games:\n"
        f"{names}"
    )


def _records_top_lines(
    game_scores: List[ScoreRow], game: str, top_n: int = 3
) -> List[str]:
    """Top N score positions (with ties expanded).

    Uses each player's personal best so the same player can't appear twice.
    Includes all players tied at any of the top N score values.
    """
    best_by_player: Dict[int, ScoreRow] = {}
    for s in game_scores:
        if s.player_id not in best_by_player or s.raw_score < best_by_player[s.player_id].raw_score:
            best_by_player[s.player_id] = s

    if not best_by_player:
        return ["  (no scores yet)"]

    entries = sorted(best_by_player.values(), key=lambda s: (s.raw_score, s.player_name))

    unique_sorted = sorted(set(s.raw_score for s in entries))
    cutoff = unique_sorted[min(top_n, len(unique_sorted)) - 1]
    top_entries = [s for s in entries if s.raw_score <= cutoff]

    lines: List[str] = []
    rank = 1
    i = 0
    while i < len(top_entries):
        score_val = top_entries[i].raw_score
        group = [e for e in top_entries if e.raw_score == score_val]
        rank_str = f"{rank}=" if len(group) > 1 else f"{rank}."
        for entry in group:
            dow_str = _DOW_SHORT[entry.puzzle_date.weekday()]
            date_str = entry.puzzle_date.strftime("%d %b %Y")
            score_str = format_raw_score(game, entry.raw_score)
            lines.append(f"  {rank_str:<3} {entry.player_name}: {score_str}  ({dow_str} {date_str})")
        rank += len(group)
        i += len(group)
    return lines


def _handle_records(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    game: str,
    *,
    group_id: int,
) -> str:
    """All-time top 3 raw scores for ``game`` (ties expanded).

    Each position lists every player whose personal best equals that
    score value, so a three-way tie at rank 1 shows all three and the
    next unique score correctly labels as rank 4.
    """
    today = la_date(now)
    game_scores = repo.list_scores(
        date_from=date(2000, 1, 1), date_to=today, group_id=group_id, game=game
    )
    if not game_scores:
        return f"No {GAME_DISPLAY[game]} scores yet — submit some to set records!"

    lines = [f"{GAME_DISPLAY[game]} — all-time top scores:"]
    lines.extend(_records_top_lines(game_scores, game, top_n=20))
    return "\n".join(lines)


def _handle_dow_records(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    game: str,
    *,
    group_id: int,
) -> str:
    """Top 3 raw scores per weekday (all time) for ``game``.

    Shows a labelled block for each weekday that has at least one score,
    with ties expanded exactly as in the all-time records command.
    """
    today = la_date(now)
    game_scores = repo.list_scores(
        date_from=date(2000, 1, 1), date_to=today, group_id=group_id, game=game
    )
    if not game_scores:
        return f"No {GAME_DISPLAY[game]} scores yet."

    lines = [f"{GAME_DISPLAY[game]} — top 3 by day of week (all time):"]
    for dow in range(7):
        dow_scores = [s for s in game_scores if s.puzzle_date.weekday() == dow]
        if not dow_scores:
            continue
        lines.append(f"\n{_DOW_SHORT[dow]}:")
        lines.extend(_records_top_lines(dow_scores, game, top_n=3))
    return "\n".join(lines)


def _handle_global_records(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    game: str,
) -> str:
    """All-time top 20 raw scores for ``game`` across every group."""
    today = la_date(now)
    game_scores = _collect_global_scores(repo, date_from=date(2000, 1, 1), date_to=today, game=game)
    if not game_scores:
        return f"No {GAME_DISPLAY[game]} scores yet — submit some to set records!"
    lines = [f"{GAME_DISPLAY[game]} — all-time top scores (all groups):"]
    lines.extend(_records_top_lines(game_scores, game, top_n=20))
    return "\n".join(lines)


def _handle_global_dow_records(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    game: str,
) -> str:
    """Top 3 raw scores per weekday (all time) for ``game`` across every group."""
    today = la_date(now)
    game_scores = _collect_global_scores(repo, date_from=date(2000, 1, 1), date_to=today, game=game)
    if not game_scores:
        return f"No {GAME_DISPLAY[game]} scores yet."
    lines = [f"{GAME_DISPLAY[game]} — top 3 by day of week (all groups, all time):"]
    for dow in range(7):
        dow_scores = [s for s in game_scores if s.puzzle_date.weekday() == dow]
        if not dow_scores:
            continue
        lines.append(f"\n{_DOW_SHORT[dow]}:")
        lines.extend(_records_top_lines(dow_scores, game, top_n=3))
    return "\n".join(lines)


# ---------------------------------------------------------------------------


def _handle_undo(
    repo: Repository,
    from_: str,
    profile_name: str,
    now: datetime,
    *,
    group_id: int,
) -> str:
    """Delete the sender's most recent submission for today's LA date.

    Restricted to today so undoing doesn't retroactively rewrite a
    recap that's already been sent. If the player has no submission
    today, we tell them — no silent no-op.
    """
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    today = la_date(now)
    my_scores = [
        s for s in repo.list_player_scores(player.id, group_id=group_id)
        if s.puzzle_date == today
    ]
    if not my_scores:
        return "Nothing to undo — you haven't submitted anything today."

    # Pick the most-recently-submitted row. list_player_scores doesn't
    # expose created_at, so fall back to the last element by list
    # order — which for InMemoryRepository is insertion order and for
    # Supabase is ``puzzle_date desc`` then primary-key order.
    target = my_scores[-1]
    deleted = repo.delete_score(
        player_id=player.id,
        game=target.game,
        puzzle_no=target.puzzle_no,
    )
    if not deleted:
        return "Couldn't undo that submission — try again in a moment."
    pretty_game = GAME_DISPLAY[target.game]
    pretty_score = format_raw_score(target.game, target.raw_score)
    return (
        f"Deleted: {pretty_game} #{target.puzzle_no} ({pretty_score}). "
        "Submit a new share if you got a better time."
    )


# Max characters allowed for a self-assigned display name. Arbitrary
# but big enough for any real name and small enough to prevent people
# from pasting novels as their handle.
_MAX_NAME_LENGTH = 30


def _parse_name_command(lower: str, original: str) -> Optional[str]:
    """Extract the new name from ``name <new>``. Uses ``original`` for
    the actual rename value so case is preserved."""
    m = re.match(r"^name\s+(.+)$", lower)
    if not m:
        return None
    # Find the same span in the original (case-preserving) text.
    start = len("name")
    while start < len(original) and original[start].isspace():
        start += 1
    return original[start:].strip()


def _handle_rename(
    repo: Repository,
    from_: str,
    profile_name: str,
    new_name: str,
) -> str:
    """Change the sender's display name. WhatsApp profile name is
    only used as a fallback on first submission — after ``name X``
    the bot uses X everywhere (recaps, stats, prizes)."""
    stripped = new_name.strip()
    if not stripped:
        return "Name can't be empty. Use: name <new name>"
    if len(stripped) > _MAX_NAME_LENGTH:
        return f"Name is too long ({len(stripped)} chars; max {_MAX_NAME_LENGTH})."
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    repo.update_player_name(player.id, stripped)
    return f"Got it — I'll call you {stripped} from now on."


def _handle_notify(
    repo: Repository,
    from_: str,
    profile_name: str,
    enabled: bool,
) -> str:
    """Toggle whether the sender gets daily/weekly recap DMs. Opted-out
    players can still submit scores; they just won't receive the
    scheduled recap."""
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    try:
        repo.set_notifications_enabled(player.id, enabled)
    except RuntimeError as exc:
        # Surface the migration-missing message so the admin sees
        # why it's not sticking instead of a generic bot error.
        return f"Can't toggle notifications yet: {exc}"
    if enabled:
        return "Notifications on — you'll receive the daily recap."
    return "Notifications off — you won't get the daily recap. Submit scores anytime."


def _handle_nag(
    repo: Repository,
    settings: Optional[Settings],
    from_: str,
    profile_name: str,
    now: datetime,
    *,
    group_id: int,
) -> str:
    """On-demand version of the morning nudge. The sender DMs
    ``nag`` / ``blast`` / ``poke`` and the bot fan-outs the standard
    "you still have these games to play" body to every active
    player who hasn't finished today, excluding the sender. Per-day
    per-sender cooldown stops one bored player from spamming the
    group fifty times.

    ``settings`` is required (for Twilio creds + tz). When missing,
    we tell the user instead of silently no-op'ing — the only people
    hitting this in a settings-less local run are developers."""
    if settings is None:
        return "`nag` needs Twilio configured. Bot's not set up to send right now."
    from .jobs import run_nag  # local import — keeps webhook import graph thin

    display_name = (profile_name or "").strip() or from_
    sender = repo.get_or_create_player(from_, display_name)
    sent, names, on_cooldown = run_nag(
        repo,
        settings,
        sender_id=sender.id,
        sender_whatsapp_id=from_,
        now=now,
        group_id=group_id,
    )
    if on_cooldown:
        return "You've already nagged today. Try again tomorrow."
    if sent == 0:
        return "Everyone's already played today — nothing to nag about."
    plural = "s" if sent != 1 else ""
    name_list = ", ".join(names)
    return f"Nudged {sent} player{plural}: {name_list}."


def _handle_taunt(
    repo: Repository,
    settings: Optional[Settings],
    from_: str,
    profile_name: str,
    now: datetime,
    *,
    kind: str,
    group_id: int,
) -> str:
    """Easter-egg broadcast: the sender DMs ``brag`` or ``gripe``, the
    bot fan-outs a competitive nudge to every other recently-active
    player. Per-day per-command cooldown stops one bored player from
    spamming the group fifty times.

    ``settings`` is required (for Twilio creds + tz). When missing,
    we tell the user instead of silently no-op'ing — the only people
    hitting this in a settings-less local run are developers."""
    if settings is None:
        return f"`{kind}` needs Twilio configured. Bot's not set up to send right now."
    from .jobs import run_taunt  # local import — keeps webhook import graph thin

    display_name = (profile_name or "").strip() or from_
    sender = repo.get_or_create_player(from_, display_name)
    sent, body, target_count = run_taunt(
        repo,
        settings,
        kind=kind,
        sender_id=sender.id,
        sender_name=sender.display_name,
        sender_whatsapp_id=from_,
        now=now,
        group_id=group_id,
    )
    if body is None:
        return f"You've already used `{kind}` today. Try again tomorrow."
    if target_count == 0:
        return "Nobody else is in the active window — taunt unsent."
    if sent == 0:
        # Audience existed but Twilio rejected every send (typically
        # because no recipient was inside their 24h customer-care
        # window). Cooldown not burned — tell the user so they can
        # retry once people are messaging again.
        plural = "s" if target_count != 1 else ""
        return (
            f"Tried `{kind}` to {target_count} player{plural} but all sends failed "
            f"(probably the WhatsApp 24h window). Cooldown not burned — try again later."
        )
    suffix = "Consequences pending." if kind == "brag" else "Sympathy optional."
    plural = "s" if sent != 1 else ""
    return (
        f"Sent `{kind}` to {sent} player{plural}. {suffix}\n\n"
        f"They got:\n> {body}"
    )


def _validate_group_name(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """Strip + validate a group name. Returns ``(name, error)`` —
    exactly one is non-None. Empty / overlong names get an explicit
    error rather than silently truncating."""
    name = raw.strip()
    if not name:
        return None, "Group name can't be empty. Use: group <name>"
    if len(name) > _MAX_GROUP_NAME_LENGTH:
        return (
            None,
            f"Group name too long ({len(name)} chars; "
            f"max {_MAX_GROUP_NAME_LENGTH}).",
        )
    return name, None


def _handle_group(
    repo: Repository,
    sender_player_id: int,
    raw_name: str,
    *,
    sender_group_id: Optional[int],
) -> str:
    """Create-or-join: ``group <name>``. New name → creates a new
    group and signs the sender in. Existing name → joins (or stays
    in, when the sender is already in that group).

    Used both as the onboarding command (``sender_group_id is None``)
    and as a group-move shortcut. ``switch`` is the explicit
    move-only sibling for users who want a guard against typos
    creating new groups by accident."""
    name, error = _validate_group_name(raw_name)
    if error is not None:
        return error
    assert name is not None  # for type-checker
    existing = repo.find_group_by_name(name)
    if existing is None:
        created = repo.get_or_create_group(name)
        repo.set_player_group(sender_player_id, created.id)
        return (
            f"Created group `{created.name}` and signed you in. "
            "Submit a LinkedIn share to record your first score, or "
            "send `help` for the command list."
        )
    repo.set_player_group(sender_player_id, existing.id)
    if existing.id == sender_group_id:
        return f"You're already in `{existing.name}`."
    return (
        f"Joined group `{existing.name}`. Send `help` for the command "
        "list, or paste a LinkedIn share to log a score."
    )


def _handle_switch(
    repo: Repository,
    sender_player_id: int,
    raw_name: str,
    *,
    sender_group_id: Optional[int],
    sender_group_name: Optional[str],
) -> str:
    """``switch <name>``: move to an existing group only. Errors with
    a hint to use ``group <name>`` if the target doesn't exist —
    deliberately stricter than ``group <name>`` so a typo doesn't
    silently spawn a new group."""
    name, error = _validate_group_name(raw_name)
    if error is not None:
        return error
    assert name is not None
    target = repo.find_group_by_name(name)
    if target is None:
        return (
            f"No group called `{raw_name.strip()}`. "
            f"Run `group {raw_name.strip()}` to create it instead."
        )
    if target.id == sender_group_id:
        return f"You're already in `{target.name}`."
    repo.set_player_group(sender_player_id, target.id)
    old_name = sender_group_name or "your old group"
    return (
        f"Switched to `{target.name}`. Past scores stay with "
        f"`{old_name}`; new submissions count for `{target.name}` "
        "from now on."
    )


def _onboarding_prompt() -> str:
    """Welcome message for un-onboarded senders. Combines the
    group-pick instruction with a short tour of how the bot works
    so first-timers can self-onboard from a single DM. Sized to
    fit comfortably under WhatsApp's 1600-char ceiling."""
    return (
        "Welcome! This is a LinkedIn games score tracker for friend "
        "groups. Here's how it works:\n"
        "\n"
        "GROUPS\n"
        "Join a group to share a leaderboard with your friends. "
        "Scores, recaps, and rankings stay inside your group — "
        "people in other groups don't see your activity.\n"
        "\n"
        "  group <name> — create a new group or join an existing one\n"
        "  switch <name> — move to another group later "
        "(past scores stay where they were earned)\n"
        "\n"
        "Group names are case-insensitive.\n"
        "\n"
        "POSTING SCORES\n"
        "Once you're in a group, paste the LinkedIn share text. "
        "Example:\n"
        "  Queens #714\n"
        "  0:10\n"
        "Any of the 7 LinkedIn games (Queens, Tango, Pinpoint, "
        "Crossclimb, Zip, Patches, Mini Sudoku) works the same way "
        "— just paste what LinkedIn gives you.\n"
        "\n"
        "WHAT TO EXPECT\n"
        "- Daily recap when LinkedIn flips puzzles (~5pm Sydney)\n"
        "- Sunday weekly wrap with prizes + final standings\n"
        "- Morning nudge if you haven't played yet\n"
        "- Escalating reminders before the daily reset\n"
        "- `notify off` to mute recap DMs anytime\n"
        "\n"
        "Run `group <name>` to start. Send `help` once you've "
        "joined for the full command list."
    )


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

    # Resolve sender + their current group. ``sender_group`` is None
    # for brand-new players and any legacy player whose ``group_id``
    # column is still NULL — both states route to the onboarding gate
    # below. We do this *before* any command dispatch so every
    # downstream handler can rely on a valid group_id.
    display_name = (profile_name or "").strip() or from_
    sender = repo.get_or_create_player(from_, display_name)
    sender_group = (
        repo.get_group(sender.group_id) if sender.group_id else None
    )

    lower = body_stripped.lower()

    # ``42`` — Hitchhiker's-style hidden trigger that returns the
    # creator's bio. Allowed pre-onboarding so the easter egg works
    # even for first-time visitors poking around.
    if lower == "42":
        return _EASTER_EGG_42

    # Group commands always run — they're the only way out of the
    # onboarding gate, and a moved-already player still uses
    # ``group <name>`` / ``switch <name>`` to switch.
    group_match = _GROUP_RE.match(body_stripped)
    if group_match is not None:
        return _handle_group(
            repo,
            sender.id,
            group_match.group(1),
            sender_group_id=sender_group.id if sender_group else None,
        )
    switch_match = _SWITCH_RE.match(body_stripped)
    if switch_match is not None:
        return _handle_switch(
            repo,
            sender.id,
            switch_match.group(1),
            sender_group_id=sender_group.id if sender_group else None,
            sender_group_name=sender_group.name if sender_group else None,
        )

    # Onboarding gate: anyone without a group can only run ``help``,
    # ``42``, or the group commands above. Score submissions and every
    # other command get redirected to ``group <name>``.
    if sender_group is None:
        if lower in ("help", "?", "commands"):
            return _ONBOARDING_HELP_TEXT
        return _onboarding_prompt()

    group_id = sender_group.id

    # Override settings.enabled_games with this group's specific game list
    # if the group has its own games configured. This propagates automatically
    # to every handler that receives ``settings``.
    if settings is not None and sender_group.enabled_games is not None:
        from dataclasses import replace as _dc_replace
        settings = _dc_replace(settings, enabled_games=sender_group.enabled_games)

    # Check for commands before attempting score parsing
    if lower in ("help", "?", "commands"):
        return _HELP_TEXT
    if lower in ("ultrahelp", "help more", "help +"):
        return _ULTRA_HELP_TEXT
    if lower == "stats":
        return _handle_stats(repo, from_, profile_name, group_id=group_id, settings=settings, now=now)
    if lower in ("pb", "bests", "personal bests"):
        return _handle_pb(repo, from_, profile_name, group_id=group_id)
    if lower == "unparsed":
        return _handle_unparsed(repo)
    if lower in ("wrap", "week"):
        return _handle_wrap(repo, settings, now, group_id=group_id)

    if lower in ("story", "narrative", "wrap story", "week story"):
        return _handle_narrative(repo, settings, now, group_id=group_id)
    if lower in ("all", "all week"):
        return _handle_all_week(repo, settings, now, group_id=group_id)
    if lower in ("leaderboard", "standings"):
        return _handle_leaderboard(
            repo, settings, now,
            from_=from_, profile_name=profile_name, group_id=group_id,
        )
    # ``leaderboard queens`` / ``standings tango`` — per-game variant.
    for game_key in GAMES:
        display_lower = GAME_DISPLAY[game_key].lower()
        if lower in (
            f"leaderboard {game_key}",
            f"leaderboard {display_lower}",
            f"standings {game_key}",
            f"standings {display_lower}",
        ):
            return _handle_leaderboard(
                repo, settings, now, game=game_key,
                from_=from_, profile_name=profile_name, group_id=group_id,
            )
    # ``leaderboard zip tango`` / ``leaderboard zip tango crossclimb`` etc.
    multi_games = _parse_leaderboard_games(lower)
    if multi_games is not None:
        today_date = la_date(now)
        if settings is not None and settings.enabled_games and from_ is not None:
            played = _games_played_today_by(
                repo, from_, profile_name, today_date, settings.enabled_games,
                group_id=group_id,
            )
            if played != set(settings.enabled_games):
                return _no_peek_leaderboard(played, settings.enabled_games)
        parts = [
            _handle_leaderboard(
                repo, settings, now, game=g,
                from_=None, profile_name=profile_name, group_id=group_id,
            )
            for g in multi_games
        ]
        return "\n\n".join(parts)
    # ``global`` / ``all groups`` — cross-group commands.
    # Sub-commands are checked first, then the game-filter leaderboard.
    _GLOBAL_BARE_CMDS = frozenset({
        "global", "global leaderboard", "all groups", "all groups leaderboard",
    })
    _GLOBAL_RECAP_CMDS = frozenset({
        "global recap", "global yesterday", "global daily",
        "all groups recap", "all groups yesterday",
    })
    _GLOBAL_WRAP_CMDS = frozenset({
        "global wrap", "global week", "global weekly",
        "all groups wrap", "all groups week",
    })
    _GLOBAL_TIMES_CMDS = frozenset({
        "global times", "global time", "global speed",
        "all groups times", "all groups time",
    })

    if lower in _GLOBAL_RECAP_CMDS:
        return _handle_global_recap(repo, settings, now, group_id=group_id)
    if lower in _GLOBAL_WRAP_CMDS:
        return _handle_global_wrap(repo, settings, now, group_id=group_id)
    if lower in _GLOBAL_TIMES_CMDS:
        return _handle_global_times(repo, settings, now, group_id=group_id)

    # ``global records/dow <game>`` — must be checked before the generic
    # ``global <anything>`` prefix handler below, which would otherwise
    # swallow these commands and route them to the global leaderboard.
    for game_key in GAMES:
        display_lower = GAME_DISPLAY[game_key].lower()
        if lower in (
            f"global records {game_key}",
            f"global records {display_lower}",
            f"global record {game_key}",
            f"global record {display_lower}",
        ):
            return _handle_global_records(repo, settings, now, game_key)
        if lower in (
            f"global dow {game_key}",
            f"global dow {display_lower}",
        ):
            return _handle_global_dow_records(repo, settings, now, game_key)

    # ``global <date>`` — global recap for a specific date.
    _GLOBAL_DATE_PFXS = ("global ", "all groups ")
    for _gpfx in _GLOBAL_DATE_PFXS:
        if lower.startswith(_gpfx):
            _grest = lower[len(_gpfx):].strip()
            # Try as a date first (YYYY-MM-DD or relative "yesterday")
            _date_target = _resolve_leaderboard_target(_grest, la_date(now))
            if _date_target is not None:
                _gday, _gerr = _date_target
                if _gerr is not None:
                    return _gerr
                return _handle_global_recap(repo, settings, now, _gday, group_id=group_id)
            # Try as game filter for global leaderboard
            _ggames = _parse_game_names_from_words(_grest.split()) if _grest else []
            if _ggames is not None:
                return _handle_global_leaderboard(
                    repo, settings, now,
                    games=_ggames or None,
                    from_=from_, profile_name=profile_name, group_id=group_id,
                )
            break

    if lower in _GLOBAL_BARE_CMDS:
        return _handle_global_leaderboard(
            repo, settings, now,
            from_=from_, profile_name=profile_name, group_id=group_id,
        )
    # ``times`` — per-game cumulative time standings across time-based games.
    if lower in ("times", "game times", "time standings"):
        return _handle_times(repo, settings, now, group_id=group_id)
    # ``month`` / ``year`` — month-to-date and year-to-date summaries.
    if lower in ("month", "mtd", "month to date", "this month"):
        return _handle_period_leaderboard(
            repo, settings, now, period="month", group_id=group_id
        )
    if lower in ("year", "ytd", "year to date", "this year"):
        return _handle_period_leaderboard(
            repo, settings, now, period="year", group_id=group_id
        )
    # ``month <game>`` / ``year <game>`` — per-game monthly / yearly leaderboard.
    for period_key, period_aliases in (
        ("month", ("month", "mtd")),
        ("year", ("year", "ytd")),
    ):
        for game_key in GAMES:
            display_lower = GAME_DISPLAY[game_key].lower()
            if any(
                lower == f"{alias} {name}"
                for alias in period_aliases
                for name in (game_key, display_lower)
            ):
                return _handle_period_leaderboard(
                    repo, settings, now, period=period_key,
                    game=game_key, group_id=group_id,
                )
    # ``records <game>`` — all-time top 3 raw scores for one game.
    # ``dow <game>``     — best score per day of week this year for one game.
    # Bare ``records`` / ``dow`` without a game name shows the game list.
    _enabled_now = settings.enabled_games if settings else frozenset(GAMES)
    if lower in ("records", "record", "all time", "alltime"):
        return _records_game_list(_enabled_now)
    if lower in ("dow", "day records", "week best", "weekly best", "best week"):
        return _records_game_list(_enabled_now)
    for game_key in GAMES:
        display_lower = GAME_DISPLAY[game_key].lower()
        if lower in (
            f"records {game_key}",
            f"records {display_lower}",
            f"record {game_key}",
            f"record {display_lower}",
        ):
            return _handle_records(repo, settings, now, game_key, group_id=group_id)
        if lower in (
            f"dow {game_key}",
            f"dow {display_lower}",
            f"day records {game_key}",
            f"day records {display_lower}",
            f"week best {game_key}",
            f"week best {display_lower}",
        ):
            return _handle_dow_records(repo, settings, now, game_key, group_id=group_id)

    if lower in ("missing", "who", "ghosts"):
        return _handle_missing(repo, settings, now, group_id=group_id)
    if lower in ("games", "enabled"):
        return _handle_games(settings, group=sender_group)
    if lower in ("track", "tracking") or lower.startswith("track "):
        raw_args = lower[len("track"):].strip() if lower.startswith("track") else ""
        return _handle_track(repo, settings, sender_group, raw_args)
    if lower in ("rules", "scoring"):
        return _handle_rules()
    if lower in ("prize", "prizes"):
        return _handle_prizes(repo, settings, now, group_id=group_id)
    if lower == "streak":
        return _handle_streak(
            repo, from_, profile_name, now, group_id=group_id
        )
    if lower == "undo":
        return _handle_undo(
            repo, from_, profile_name, now, group_id=group_id
        )
    if lower in ("notify on", "notifications on"):
        return _handle_notify(repo, from_, profile_name, enabled=True)
    if lower in ("notify off", "notifications off"):
        return _handle_notify(repo, from_, profile_name, enabled=False)
    # ``brag`` / ``flex`` — broadcast a competitive nudge to the
    # group telling them you're crushing today.
    if lower in ("brag", "flex"):
        return _handle_taunt(
            repo, settings, from_, profile_name, now,
            kind="brag", group_id=group_id,
        )
    # ``gripe`` / ``whinge`` — broadcast a self-deprecating-but-
    # competitive nudge admitting today's a write-off.
    if lower in ("gripe", "whinge"):
        return _handle_taunt(
            repo, settings, from_, profile_name, now,
            kind="gripe", group_id=group_id,
        )
    # ``nag`` / ``blast`` / ``poke`` — manual fan-out of the morning
    # nudge body to anyone who hasn't finished today's games yet.
    if lower in ("nag", "blast", "poke"):
        return _handle_nag(
            repo, settings, from_, profile_name, now, group_id=group_id
        )

    # ``vs <name>`` — all-time head-to-head against a named opponent.
    opp = _parse_vs_command(lower)
    if opp is not None:
        return _handle_vs(
            repo, from_, profile_name, opp, group_id=group_id
        )

    # ``history`` / ``history <game>`` — personal score history.
    _hist_match, _hist_game = _parse_history_command(lower)
    if _hist_match:
        if _hist_game == "__bad__":
            return (
                "Unknown game. Try: history zip, history tango, history queens, "
                "history pinpoint, history crossclimb, history patches, "
                "history mini sudoku — or just 'history' for all games."
            )
        return _handle_history(
            repo, from_, profile_name, _hist_game,
            group_id=group_id, settings=settings,
        )

    # ``trends`` — last 4 weeks vs prior 4 weeks.
    if lower in ("trends", "trend") and settings is not None:
        return _handle_trends(
            repo, from_, profile_name, now, settings, group_id=group_id
        )

    # ``best day`` / ``worst day`` — single best/worst performance day.
    if lower in ("best day", "bestday", "my best day"):
        return _handle_best_worst_day(
            repo, from_, profile_name, "best", group_id=group_id
        )
    if lower in ("worst day", "worstday", "my worst day"):
        return _handle_best_worst_day(
            repo, from_, profile_name, "worst", group_id=group_id
        )

    # ``pace`` — current-week projection to Sunday.
    if lower in ("pace", "projection", "projected") and settings is not None:
        return _handle_pace(
            repo, from_, profile_name, now, settings, group_id=group_id
        )

    if lower in ("estimate", "what do i need", "need to win", "needed"):
        return _handle_estimate(
            repo, settings, from_, profile_name, now, group_id=group_id
        )

    # ``by day [game] [n]`` — per-DOW breakdown with optional game filter
    # and optional recency limit (last N scores).
    _byd = _parse_by_day_command(lower)
    if _byd is not None:
        _byd_game, _byd_limit = _byd
        return _handle_dow_stats(
            repo, from_, profile_name, _byd_game,
            group_id=group_id, settings=settings, limit=_byd_limit,
        )

    # ``name <new>`` — self-assigned display name.
    new_name = _parse_name_command(lower, body_stripped)
    if new_name is not None:
        return _handle_rename(repo, from_, profile_name, new_name)

    # Date-scoped leaderboard commands — checked BEFORE the recap
    # parser so bare ``yesterday`` / ``N days ago`` route to the
    # compact standings rather than the full recap.
    lb_target = _resolve_leaderboard_target(lower, la_date(now))
    if lb_target is not None:
        target_day, error = lb_target
        if error is not None:
            return error
        return _handle_leaderboard(
            repo, settings, now, target_day=target_day,
            from_=from_, profile_name=profile_name, group_id=group_id,
        )

    # Date-anchored recap commands: ``recap`` / ``today`` /
    # ``recap yesterday`` / ``recap N days ago`` / ``recap YYYY-MM-DD``.
    # Consolidated into one resolver so the handler doesn't grow a
    # branch per phrasing.
    recap_target = _resolve_recap_target(lower, la_date(now))
    if recap_target is not None:
        target_day, error = recap_target
        if error is not None:
            return error
        return _handle_recap(
            repo, settings, now, target_day=target_day,
            from_=from_, profile_name=profile_name, group_id=group_id,
        )

    # Try to parse as a game share
    parsed = parse_any(body_stripped)

    if parsed is None:
        if looks_like_score(body_stripped):
            repo.log_unparsed(from_, body_stripped)
            return (
                "That looks like a LinkedIn game share but I couldn't read "
                f"it — logged for a parser fix.\n\n{_SCORE_FORMAT_HINT}"
            )
        # Doesn't look like a score and doesn't match a command —
        # reply with a one-liner that points at ``help`` rather than
        # dumping the full command list every time. The bot operates
        # in 1:1 DMs, so the user can pull help when they want it.
        return "Sorry, I don't understand. Send `help` for approved commands."

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

    # ``sender`` was resolved at the top of the dispatcher; reuse it
    # so the insert path doesn't re-fetch the player. The score is
    # tied to the sender's *current* group (set above as ``group_id``)
    # — switching groups later doesn't move existing scores.
    player = sender
    # Anchor the puzzle day in LA time — that's when LinkedIn rolls, so a
    # 4:45pm Sydney submission (still yesterday in LA) files under
    # yesterday's LA date and a 5:15pm one lands under today's. Keeps
    # daily/weekly windows consistent with LinkedIn's own puzzle days.
    puzzle_date = la_date(now)

    inserted = repo.insert_score(
        player_id=player.id,
        group_id=group_id,
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

    # Personal-best / worst-ever and day-complete bodies are folded
    # into the score-confirmation reply (one consolidated message
    # instead of two or three Twilio sends). Each call is wrapped in
    # try/except so a notification failure can never sink the ack.
    pb_body: Optional[str] = None
    try:
        from .notifications import maybe_notify_personal_best

        pb_body = maybe_notify_personal_best(
            repo,
            settings,
            player=player,
            game=parsed.game,
            new_raw=parsed.raw_score,
            today=puzzle_date,
            deliver=False,
            group_id=group_id,
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "maybe_notify_personal_best failed after insert by player %s",
            player.id,
        )

    day_complete_body: Optional[str] = None
    try:
        from .notifications import maybe_notify_day_complete

        day_complete_body = maybe_notify_day_complete(
            repo,
            settings,
            player=player,
            today=puzzle_date,
            enabled_games=enabled_games,
            deliver=False,
            group_id=group_id,
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "maybe_notify_day_complete failed after insert by player %s",
            player.id,
        )

    # Event-driven early recap: if this submission completes the day
    # (everyone's played all enabled games), fire the recap right
    # away rather than waiting for the LA-midnight cron. Wrapped in
    # try/except so a failure in the recap path can't sink the
    # acknowledgment of a perfectly valid submission.
    if settings is not None:
        try:
            from .jobs import maybe_fire_early_recap

            maybe_fire_early_recap(repo, settings, now=now, group_id=group_id)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "maybe_fire_early_recap failed after insert by player %s",
                player.id,
            )

    # Group broadcasts: photo finish + comeback (fire-and-forget; never
    # block the submission ack if these fail).
    if settings is not None and parsed.game in enabled_games:
        from .jobs import _group_recap_to as _group_recap_to_from_group
        _recap_to = _group_recap_to_from_group(settings, sender_group)
        try:
            from .notifications import maybe_broadcast_photo_finish
            maybe_broadcast_photo_finish(
                repo, settings,
                group_id=group_id,
                today=puzzle_date,
                group_recap_to=_recap_to,
                enabled_games=enabled_games,
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "maybe_broadcast_photo_finish failed after insert by player %s",
                player.id,
            )
        # Badge checks after submission.
        try:
            from .badges import check_badges_after_submission, notify_new_badges
            new_badges = check_badges_after_submission(
                repo,
                player_id=player.id,
                group_id=group_id,
                game=parsed.game,
                new_raw=parsed.raw_score,
                today=puzzle_date,
                enabled_games=enabled_games,
            )
            if new_badges:
                notify_new_badges(repo, settings, player=player, badges=new_badges)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "badge checks failed after insert by player %s", player.id
            )

        try:
            from .notifications import maybe_broadcast_comeback
            maybe_broadcast_comeback(
                repo, settings,
                player_id=player.id,
                player_name=player.display_name,
                group_id=group_id,
                today=puzzle_date,
                group_recap_to=_recap_to,
                enabled_games=enabled_games,
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "maybe_broadcast_comeback failed after insert by player %s",
                player.id,
            )

    confirmation = (
        f"Got it, {player.display_name}. "
        f"{pretty_game} #{parsed.puzzle_no}: {pretty_new}.{off_note}"
    )
    extras = [b for b in (pb_body, day_complete_body) if b]
    if extras:
        return confirmation + "\n\n" + "\n\n".join(extras)
    return confirmation
