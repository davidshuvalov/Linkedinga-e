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
from .db import Repository, ScoreRow
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
# the fallback reply when the bot can't classify a message. Grouped
# into Read / You / Mutating sections so the wall of commands is
# scannable — we have a lot now.
_HELP_TEXT = (
    "Commands:\n"
    "  Look at scores:\n"
    "    recap / today / yesterday / \"3 days ago\" / recap YYYY-MM-DD\n"
    "    week / wrap — weekly wrap\n"
    "    all / history — every round this week\n"
    "    leaderboard / standings (+ optional game, e.g. \"leaderboard queens\")\n"
    "    prizes — live prize snapshot\n"
    "    missing / who — who hasn't played today\n"
    "    games — which games are tracked\n"
    "    rules / scoring — how points work\n"
    "\n"
    "  About you:\n"
    "    stats — your all-time stats\n"
    "    pb / bests — your personal bests\n"
    "    streak — your current streak\n"
    "    vs <name> — head-to-head all-time\n"
    "\n"
    "  Change things:\n"
    "    undo — delete today's last submission\n"
    "    name <new> — change your display name\n"
    "    notify on / notify off — toggle daily recap DMs\n"
    "    help / ? — show this list\n"
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


# ---------------------------------------------------------------------------
# Read-only utility commands (added in the "do it all" batch)
# ---------------------------------------------------------------------------


def _fmt_pts(value: float) -> str:
    """Compact points rendering: ``5`` for ints, ``5.8`` for fractions."""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}"


def _week_filtered_scores(
    repo: Repository, settings: Settings, now: datetime
) -> Tuple[List[ScoreRow], date, date, date]:
    """Common prologue for week-scoped commands.

    Fetches the whole current LA week, filters to enabled games, and
    returns ``(filtered_scores, today, monday, sunday)``.
    """
    from .puzzles import week_bounds

    today = la_date(now)
    monday, sunday = week_bounds(today)
    week_scores = repo.list_scores(date_from=monday, date_to=sunday)
    filtered = [s for s in week_scores if s.game in settings.enabled_games]
    return filtered, today, monday, sunday


def _handle_leaderboard(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
    game: Optional[str] = None,
) -> str:
    """Weekly leaderboard — overall or restricted to one game.

    Overall mode (``game is None``) is the same table used by the
    daily/weekly recap's "Week so far" block, surfaced on its own
    so users can pull just the standings without the per-game
    sections. Game-filtered mode tallies weekly points for that
    single game only, still using :func:`assign_daily_points`.
    """
    if settings is None:
        return "Leaderboard isn't available in this context."
    from .scoring import assign_daily_points, weekly_leaderboard

    filtered, today, _, _ = _week_filtered_scores(repo, settings, now)
    if not filtered:
        return "No scores yet this week."

    if game is None:
        lb = weekly_leaderboard(filtered)
        lines = [f"Week so far — {today.strftime('%a %d %b %Y')}:"]
        for i, p in enumerate(lb, start=1):
            rounds_word = "1 round" if p.submissions == 1 else f"{p.submissions} rounds"
            lines.append(
                f"  {i}. {p.player_name}: {_fmt_pts(p.total_points)} pts ({rounds_word})"
            )
        return "\n".join(lines)

    # Per-game leaderboard: tally competitive_score results per player.
    groups: Dict[int, List[ScoreRow]] = {}
    player_names: Dict[int, str] = {}
    for s in filtered:
        if s.game != game:
            continue
        groups.setdefault(s.puzzle_no, []).append(s)
        player_names[s.player_id] = s.player_name

    if not groups:
        return f"No {GAME_DISPLAY[game]} scores yet this week."

    totals: Dict[int, float] = {}
    submissions: Dict[int, int] = {}
    for group_scores in groups.values():
        for pid, pts in assign_daily_points(group_scores).items():
            totals[pid] = totals.get(pid, 0.0) + pts
        for s in group_scores:
            submissions[s.player_id] = submissions.get(s.player_id, 0) + 1

    ranked = sorted(
        totals.items(),
        key=lambda kv: (-kv[1], kv[0]),
    )
    lines = [f"{GAME_DISPLAY[game]} — week so far:"]
    for i, (pid, pts) in enumerate(ranked, start=1):
        subs = submissions.get(pid, 0)
        subs_word = "1 round" if subs == 1 else f"{subs} rounds"
        lines.append(
            f"  {i}. {player_names[pid]}: {_fmt_pts(round(pts, 1))} pts ({subs_word})"
        )
    return "\n".join(lines)


def _handle_missing(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
) -> str:
    """List players who submitted earlier this week but skipped today.

    Same logic the daily recap's passive-aggressive nudge uses, but
    surfaced on demand so a player can check who's still to play
    without waiting for the 5pm recap.
    """
    if settings is None:
        return "Missing-today check isn't available in this context."
    filtered, today, _, _ = _week_filtered_scores(repo, settings, now)
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


def _handle_pb(repo: Repository, from_: str, profile_name: str) -> str:
    """Just the personal-bests block from ``stats`` — shorter and
    more scanable when a player only cares about their PBs."""
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    scores = repo.list_player_scores(player.id)
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


def _handle_games(settings: Optional[Settings]) -> str:
    """Show which games count toward the leaderboard vs which are
    parsed-but-untracked. Helps new players understand why their
    Pinpoint score didn't show up in the wrap."""
    if settings is None:
        return "Game list isn't available in this context."
    enabled_display = [
        GAME_DISPLAY[g] for g in GAME_DISPLAY_ORDER if g in settings.enabled_games
    ]
    disabled_display = [
        GAME_DISPLAY[g] for g in GAME_DISPLAY_ORDER if g not in settings.enabled_games
    ]
    lines = [f"Tracked games ({len(enabled_display)}):"]
    for g in enabled_display:
        lines.append(f"  - {g}")
    if disabled_display:
        lines.append("")
        lines.append("Not tracked (scores still stored, not scored):")
        for g in disabled_display:
            lines.append(f"  - {g}")
    return "\n".join(lines)


# Static scoring explanation. Stable text — kept inline so it's easy
# to tweak when the rules change without hunting through templates.
_RULES_TEXT = (
    "Scoring — short version:\n"
    "\n"
    "3–5 player time-based rounds (no ties) use competitive scoring:\n"
    "  * Base points by rank: 5/4/3/2/1.\n"
    "  * Tight game (times within 50% of fastest) — base points stand.\n"
    "  * Clear winner (2nd is >1.5x slower than 1st) — 1st gets a\n"
    "    bonus up to +2, debited proportionally from others.\n"
    "  * Front cluster (top 3 close, big drop after 3rd) — top 3\n"
    "    split a 1.5-pt bonus weighted by 1/time.\n"
    "  * Floor 0.5; total preserved; rounded to 1 d.p.\n"
    "\n"
    "Everything else (pinpoint, ties, 6+ players) uses legacy ranks:\n"
    "  1st=5, 2nd=4, 3rd=3, 4th=2, 5th=1. Tied players share the\n"
    "  ceiling of the average.\n"
    "\n"
    "Weekly prizes: Most firsts, Most lasts, Best average (≥5 subs),\n"
    "Fastest total time (≥5 time-based rounds)."
)


def _handle_rules() -> str:
    return _RULES_TEXT


def _handle_prizes(
    repo: Repository,
    settings: Optional[Settings],
    now: datetime,
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

    filtered, _, _, _ = _week_filtered_scores(repo, settings, now)
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
    if prizes.fastest_total_time is not None:
        ft = prizes.fastest_total_time
        lines.append(
            f"  Fastest total time: {ft.player_name} "
            f"({_format_seconds(ft.total_time)} / {ft.time_based_submissions} rounds)"
        )
    if len(lines) == 1:
        lines.append("  Nobody has qualified yet.")
    return "\n".join(lines)


def _handle_streak(
    repo: Repository,
    from_: str,
    profile_name: str,
    now: datetime,
) -> str:
    """Sender's current consecutive-days submission streak.

    "Current" means the most recent unbroken run ending today or
    yesterday (so a player doesn't lose their streak mid-morning just
    because they haven't played yet). Days are counted in LA time to
    match the puzzle-rollover convention.
    """
    display_name = (profile_name or "").strip() or from_
    player = repo.get_or_create_player(from_, display_name)
    scores = repo.list_player_scores(player.id)
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
) -> str:
    """All-time head-to-head between the sender and a named opponent.

    Matches opponent by display name (case-insensitive). Counts only
    rounds both players submitted (same ``game`` + ``puzzle_no``).
    Per-game wins/losses/ties broken out plus an overall total.
    """
    display_name = (profile_name or "").strip() or from_
    me = repo.get_or_create_player(from_, display_name)
    my_scores = repo.list_player_scores(me.id)
    if not my_scores:
        return "You haven't submitted any scores yet — nothing to compare."

    # Walk every ScoreRow in the week-wide list_scores window to find
    # the opponent by name. We use a wide date window since this is
    # all-time comparison.
    # There isn't a "find player by name" method, so scan all-time by
    # iterating scores back to epoch — pragmatic given group size.
    all_scores = repo.list_scores(date_from=date(2000, 1, 1), date_to=date(2100, 1, 1))
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
# Mutating commands
# ---------------------------------------------------------------------------


def _handle_undo(
    repo: Repository,
    from_: str,
    profile_name: str,
    now: datetime,
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
        s for s in repo.list_player_scores(player.id) if s.puzzle_date == today
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
    if lower in ("pb", "bests", "personal bests"):
        return _handle_pb(repo, from_, profile_name)
    if lower == "unparsed":
        return _handle_unparsed(repo)
    if lower in ("wrap", "week"):
        return _handle_wrap(repo, settings, now)
    if lower in ("all", "all week", "history"):
        return _handle_all_week(repo, settings, now)
    if lower in ("leaderboard", "standings"):
        return _handle_leaderboard(repo, settings, now)
    # ``leaderboard queens`` / ``standings tango`` — per-game variant.
    for game_key in GAMES:
        display_lower = GAME_DISPLAY[game_key].lower()
        if lower in (
            f"leaderboard {game_key}",
            f"leaderboard {display_lower}",
            f"standings {game_key}",
            f"standings {display_lower}",
        ):
            return _handle_leaderboard(repo, settings, now, game=game_key)
    if lower in ("missing", "who", "ghosts"):
        return _handle_missing(repo, settings, now)
    if lower in ("games", "enabled"):
        return _handle_games(settings)
    if lower in ("rules", "scoring"):
        return _handle_rules()
    if lower in ("prize", "prizes"):
        return _handle_prizes(repo, settings, now)
    if lower == "streak":
        return _handle_streak(repo, from_, profile_name, now)
    if lower == "undo":
        return _handle_undo(repo, from_, profile_name, now)
    if lower in ("notify on", "notifications on"):
        return _handle_notify(repo, from_, profile_name, enabled=True)
    if lower in ("notify off", "notifications off"):
        return _handle_notify(repo, from_, profile_name, enabled=False)

    # ``vs <name>`` — all-time head-to-head against a named opponent.
    opp = _parse_vs_command(lower)
    if opp is not None:
        return _handle_vs(repo, from_, profile_name, opp)

    # ``name <new>`` — self-assigned display name.
    new_name = _parse_name_command(lower, body_stripped)
    if new_name is not None:
        return _handle_rename(repo, from_, profile_name, new_name)

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

    # Event-driven early recap: if this submission completes the day
    # (everyone's played all enabled games), fire the recap right
    # away rather than waiting for the LA-midnight cron. Wrapped in
    # try/except so a failure in the recap path can't sink the
    # acknowledgment of a perfectly valid submission.
    if settings is not None:
        try:
            from .jobs import maybe_fire_early_recap

            maybe_fire_early_recap(repo, settings, now=now)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "maybe_fire_early_recap failed after insert by player %s",
                player.id,
            )

    return (
        f"Got it, {player.display_name}. "
        f"{pretty_game} #{parsed.puzzle_no}: {pretty_new}.{off_note}"
    )
