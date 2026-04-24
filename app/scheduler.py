"""Recap/wrap text builders.

:func:`daily_recap` and :func:`weekly_wrap` are pure functions that take a
batch of :class:`~app.db.ScoreRow` records and return a WhatsApp-friendly
formatted string — one message per call, kept short enough to copy-paste
or forward from a 1:1 DM into the friends' group chat.

Both accept an ``enabled_games`` set; scores for disabled games are
silently excluded from the output and from the scoring totals.

Both also accept the **whole week's scores**, not just one day's, so
the daily message can show a running "Week so far" leaderboard and the
weekly wrap can reconcile the final standings.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from .db import ScoreRow
from .parsers import GAME_DISPLAY, GAME_DISPLAY_ORDER, format_raw_score
from .puzzles import (
    is_last_day_of_month,
    is_last_day_of_year,
    month_bounds,
    year_bounds,
)
from .scoring import (
    _NON_TIME_GAMES,
    assign_daily_points,
    game_leaders,
    prize_allocations,
    weekly_leaderboard,
)

_ALL_GAMES = frozenset(GAME_DISPLAY)

# Passive-aggressive templates for players who submitted earlier this
# week but ghosted today. ``{names}`` is interpolated with a
# comma-separated join. Selection rotates deterministically on
# ``day.toordinal()`` so every recipient sees the same line on the
# same day but the zinger changes across days.
_MISSING_TODAY_TEMPLATES = (
    "Still MIA today: {names}. The puzzles aren't going to solve themselves.",
    "Today's no-shows: {names}. Hoping everything's alright.",
    "Haven't heard from {names} today. Suspicious.",
    "{names}: we noticed. The leaderboard noticed. LinkedIn noticed.",
    "Where art thou, {names}? Your rank is slipping.",
    "{names} are busy doing literally anything other than today's puzzles.",
    "Benched today: {names}. Room on the couch for snacks and excuses.",
)


def _format_seconds(total: int) -> str:
    """Render a seconds count as ``M:SS``. Minutes can exceed 60 —
    weekly time totals sometimes cross an hour and we keep one
    consistent format so rows line up."""
    minutes, seconds = divmod(total, 60)
    return f"{minutes}:{seconds:02d}"


def _pts(points: float) -> str:
    """Render a points value — integer-valued floats stay clean
    ("5 pts", "4 pts"), fractional values render as ``N.N pts``
    ("7.0 pts", "3.2 pts"). Competitive scoring emits floats;
    legacy rank rounds still produce integers and should display
    the same way they always did.
    """
    if abs(points - round(points)) < 1e-9:
        p = int(round(points))
        return "1 pt" if p == 1 else f"{p} pts"
    return f"{points:.1f} pts"


def _rounds_word(n: int) -> str:
    """Submissions count (every individual round played). ``distinct_games``
    — the number of *game types* touched — tops out at 7 and undercounts
    people who replay the same game daily; ``submissions`` grows linearly
    with participation which is what users actually want to see."""
    return "1 round" if n == 1 else f"{n} rounds"


def _player_game_totals(
    scores: Sequence[ScoreRow], player_id: int, game: str
) -> Tuple[int, int]:
    """Return ``(submissions, total_seconds)`` for one ``(player, game)``.

    Pinpoint is a guess count, not seconds, so its raw_score never
    contributes to the time total — matches the convention used by
    :func:`weekly_leaderboard`.
    """
    subs = 0
    secs = 0
    is_time_game = game not in _NON_TIME_GAMES
    for s in scores:
        if s.player_id != player_id or s.game != game:
            continue
        subs += 1
        if is_time_game:
            secs += s.raw_score
    return subs, secs


# ---------------------------------------------------------------------------
# Shared section builders
# ---------------------------------------------------------------------------


def _per_game_sections(
    day: date,
    day_scores: Sequence[ScoreRow],
) -> List[str]:
    """Build the per-game rankings block for ``day``.

    Returns a list of lines; caller decides how to join with surrounding
    content. Games with no submissions for the day are silently skipped.
    Groups are ordered by ``GAME_DISPLAY_ORDER`` and then by puzzle number
    (usually one puzzle per game per day, but this is future-proof).
    """
    groups: Dict[Tuple[str, int], List[ScoreRow]] = {}
    for s in day_scores:
        groups.setdefault((s.game, s.puzzle_no), []).append(s)

    lines: List[str] = []
    for game in GAME_DISPLAY_ORDER:
        matching = sorted(
            (key for key in groups if key[0] == game),
            key=lambda k: k[1],
        )
        for key in matching:
            group_scores = sorted(groups[key], key=lambda s: s.raw_score)
            points_map = assign_daily_points(group_scores)
            lines.append(f"{GAME_DISPLAY[game]} #{key[1]}")
            for s in group_scores:
                pts = points_map[s.player_id]
                lines.append(
                    f"  {s.player_name} — "
                    f"{format_raw_score(game, s.raw_score)} ({_pts(pts)})"
                )
            lines.append("")
    # Drop the trailing blank so the caller controls spacing.
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _rank_delta_suffix(
    prior_ranks: Dict[int, int], player_id: int, current_rank: int
) -> str:
    """Render ``↑N`` / ``↓N`` / ``NEW`` based on how the player's rank
    moved since ``prior_ranks``. Empty string when the position is
    unchanged or when there's nothing to compare against — readers
    only care about *changes*, so a sea of ``=`` markers on an
    otherwise stable board just added noise."""
    prev = prior_ranks.get(player_id)
    if prev is None:
        return " NEW"
    if prev == current_rank:
        return ""
    if prev > current_rank:
        return f" ↑{prev - current_rank}"
    return f" ↓{current_rank - prev}"


def _weekly_leaderboard_lines(
    week_scores: Sequence[ScoreRow],
    title: str = "Week so far",
    prior_scores: Optional[Sequence[ScoreRow]] = None,
) -> List[str]:
    """Render the cumulative weekly leaderboard as a compact list.

    Used by both the daily recap (midweek: "Week so far") and the
    weekly wrap (final: "Week totals"). Returns empty list if nobody
    has submitted anything this week.

    When ``prior_scores`` is non-empty (typically the week's scores
    before ``day``), each row gets a position-change suffix comparing
    today's rank to the prior standings. On Monday there's no prior
    standings so arrows are omitted entirely.
    """
    lb = weekly_leaderboard(week_scores)
    if not lb:
        return []

    prior_lb = weekly_leaderboard(list(prior_scores)) if prior_scores else []
    prior_ranks = {p.player_id: i for i, p in enumerate(prior_lb, start=1)}
    show_arrows = bool(prior_lb)

    lines: List[str] = [f"{title}:"]
    for i, p in enumerate(lb, start=1):
        suffix = (
            _rank_delta_suffix(prior_ranks, p.player_id, i)
            if show_arrows
            else ""
        )
        lines.append(
            f"  {i}. {p.player_name}: {_pts(p.total_points)} "
            f"(T: {_format_seconds(p.total_time)}, G:{p.submissions}){suffix}"
        )
    return lines


def _missing_today_line(
    day: date,
    week_scores: Sequence[ScoreRow],
    day_scores: Sequence[ScoreRow],
) -> Optional[str]:
    """Passive-aggressive callout naming players who played earlier
    this week but skipped today.

    Returns ``None`` when nobody is missing (all weekly participants
    also played today, or nobody's played all week). Defines "missing"
    as the set of player_ids active earlier in the week minus those
    active today — so we don't nag people who are new to the group or
    weren't expected to play.
    """
    today_ids = {s.player_id for s in day_scores}
    # Preserve first-seen order so the output is deterministic regardless
    # of dict-ordering quirks between Python builds.
    missing: List[str] = []
    seen: set[int] = set()
    for s in week_scores:
        if s.puzzle_date >= day:
            continue  # today or future — not "earlier this week"
        if s.player_id in today_ids:
            continue
        if s.player_id in seen:
            continue
        seen.add(s.player_id)
        missing.append(s.player_name)

    if not missing:
        return None

    template = _MISSING_TODAY_TEMPLATES[
        day.toordinal() % len(_MISSING_TODAY_TEMPLATES)
    ]
    return template.format(names=", ".join(missing))


_GAME_STANDINGS_TOP_N = 3


def _per_game_running_totals(
    week_scores: Sequence[ScoreRow],
) -> List[str]:
    """Per-game running point totals for the week so far.

    Renders one line per game with the top 3 players' cumulative
    points in that game, sorted descending so the current game
    leader is first. Capping at 3 keeps the block skimmable — the
    full leaderboard is already in the ``Week so far`` block below.
    Only games with any submissions this week appear; games are
    ordered by :data:`GAME_DISPLAY_ORDER` so the block is stable.
    Returns empty list if nothing's been played this week.
    """
    if not week_scores:
        return []

    # Group by (game, puzzle_no) so we can hand daily rounds to
    # assign_daily_points — same helper the daily per-game block uses.
    groups: Dict[Tuple[str, int], List[ScoreRow]] = {}
    for s in week_scores:
        groups.setdefault((s.game, s.puzzle_no), []).append(s)

    per_game_totals: Dict[str, Dict[int, float]] = {}
    player_names: Dict[int, str] = {}
    for (game, _), group_scores in groups.items():
        bucket = per_game_totals.setdefault(game, {})
        for pid, pts in assign_daily_points(group_scores).items():
            bucket[pid] = bucket.get(pid, 0.0) + pts
    for s in week_scores:
        player_names[s.player_id] = s.player_name

    def _compact(val: float) -> str:
        """Integer-valued totals stay as ``5``; fractional as ``5.8``."""
        if abs(val - round(val)) < 1e-9:
            return str(int(round(val)))
        return f"{val:.1f}"

    lines: List[str] = ["Game standings (week):"]
    any_rendered = False
    for game in GAME_DISPLAY_ORDER:
        totals = per_game_totals.get(game)
        if not totals:
            continue
        # Sort players by (points desc, player_id asc) for stable
        # order, then take only the top 3 — full leaderboard is in
        # the ``Week so far`` block.
        ranked = sorted(
            totals.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )[:_GAME_STANDINGS_TOP_N]
        parts = [
            f"{player_names.get(pid, '')} {_compact(round(pts, 1))}"
            for pid, pts in ranked
        ]
        lines.append(f"  {GAME_DISPLAY[game]}: " + ", ".join(parts))
        any_rendered = True

    return lines if any_rendered else []


# ---------------------------------------------------------------------------
# daily_recap
# ---------------------------------------------------------------------------


def daily_recap(
    day: date,
    week_scores: Sequence[ScoreRow],
    enabled_games: FrozenSet[str] = _ALL_GAMES,
    *,
    month_scores: Optional[Sequence[ScoreRow]] = None,
    year_scores: Optional[Sequence[ScoreRow]] = None,
) -> str:
    """Format a daily recap for ``day``.

    Contents:
    - Per-game rankings for every round played that day.
    - Running "Week so far" cumulative leaderboard across the whole week
      (which is why ``week_scores`` is the **whole week**, not just
      ``day``'s slice).
    - Optional "Month totals" / "Year totals" blocks when ``month_scores``
      / ``year_scores`` are provided. Callers (jobs.render_daily)
      decide when to pass them — typically on the last day of the
      month / year.

    ``week_scores`` must include ``day``'s scores. Scores for disabled
    games are filtered out before rendering and before leaderboard
    aggregation.
    """
    header = f"Daily recap — {day.strftime('%a %d %b %Y')}"

    week_filtered = [s for s in week_scores if s.game in enabled_games]
    day_scores = [s for s in week_filtered if s.puzzle_date == day]

    if not day_scores:
        return f"{header}\n\nNo scores yet.\n"

    lines: List[str] = [header, ""]
    lines.extend(_per_game_sections(day, day_scores))

    # Per-game running totals across the whole week — complements the
    # overall "Week so far" leaderboard below by showing who's ahead in
    # each game individually, not just on aggregate points.
    game_totals_lines = _per_game_running_totals(week_filtered)
    if game_totals_lines:
        lines.append("")
        lines.extend(game_totals_lines)

    # Prior standings = the week up to but not including today, so
    # the position-change arrows compare today's board to yesterday's.
    prior_scores = [s for s in week_filtered if s.puzzle_date < day]
    lb_lines = _weekly_leaderboard_lines(
        week_filtered,
        title="Week so far",
        prior_scores=prior_scores,
    )
    if lb_lines:
        lines.append("")
        lines.extend(lb_lines)

    # Month / year totals — opt-in via params. Caller-driven so the
    # formatter stays pure (no date math to decide when to include).
    month_block = _period_totals_block(
        month_scores, enabled_games, f"Month totals — {day.strftime('%b %Y')}"
    )
    if month_block:
        lines.append("")
        lines.extend(month_block)

    year_block = _period_totals_block(
        year_scores, enabled_games, f"Year totals — {day.year}"
    )
    if year_block:
        lines.append("")
        lines.extend(year_block)

    # Passive-aggressive nudge for players who played earlier this
    # week but skipped today. Sits at the bottom where it won't
    # compete with the actual scores.
    nag = _missing_today_line(day, week_filtered, day_scores)
    if nag is not None:
        lines.append("")
        lines.append(nag)

    return "\n".join(lines).rstrip() + "\n"


def _period_totals_block(
    scores: Optional[Sequence[ScoreRow]],
    enabled_games: FrozenSet[str],
    title: str,
) -> List[str]:
    """Render a period-totals block (Month / Year) or empty list.

    ``scores`` is the full period — callers pass the month or year's
    scores when they want the block rendered; passing ``None`` (or
    an empty list) produces nothing. Disabled games are filtered out
    for consistency with the weekly block.
    """
    if not scores:
        return []
    filtered = [s for s in scores if s.game in enabled_games]
    if not filtered:
        return []
    return _weekly_leaderboard_lines(filtered, title=title)


# ---------------------------------------------------------------------------
# weekly_wrap
# ---------------------------------------------------------------------------


def weekly_wrap(
    week_start: date,
    week_end: date,
    week_scores: Sequence[ScoreRow],
    enabled_games: FrozenSet[str] = _ALL_GAMES,
    *,
    month_scores: Optional[Sequence[ScoreRow]] = None,
    year_scores: Optional[Sequence[ScoreRow]] = None,
) -> str:
    """Format a weekly wrap covering ``[week_start, week_end]`` inclusive.

    Contents (all in one message):

    - Per-game rankings for ``week_end`` (the final day — usually
      Sunday LA — so Sunday's scores still get their own spotlight).
    - Final "Week totals" leaderboard (same shape as the daily
      "Week so far" block, but named differently to signal closure).
    - Per-game **weekly** winners — who accumulated the most points
      in each game across the whole week.
    - Three prizes: Most firsts, Most lasts, Best average.

    Kept under ~40 lines so the whole message copies into one forward.
    """
    header = (
        f"Weekly wrap — "
        f"{week_start.strftime('%a %d %b')} to "
        f"{week_end.strftime('%a %d %b %Y')}"
    )
    week_filtered = [
        s for s in week_scores
        if week_start <= s.puzzle_date <= week_end
        and s.game in enabled_games
    ]

    if not week_filtered:
        return f"{header}\n\nNo scores this week.\n"

    lines: List[str] = [header, ""]

    # Final day's per-game rankings
    final_day_scores = [s for s in week_filtered if s.puzzle_date == week_end]
    if final_day_scores:
        lines.append(f"{week_end.strftime('%a %d %b')}:")
        lines.append("")
        lines.extend(_per_game_sections(week_end, final_day_scores))
        lines.append("")

    # Week totals leaderboard
    lines.extend(_weekly_leaderboard_lines(week_filtered, title="Week totals"))

    # Per-game weekly winners
    leaders = game_leaders(week_filtered)
    if leaders:
        lines.append("")
        lines.append("Game winners:")
        for game in GAME_DISPLAY_ORDER:
            gl = next((g for g in leaders if g.game == game), None)
            if gl is not None:
                g_subs, g_time = _player_game_totals(
                    week_filtered, gl.player_id, game
                )
                # Pinpoint tracks guess counts, not seconds — the
                # total would always render as 0:00, so drop T:.
                if game in _NON_TIME_GAMES:
                    stats = f"G:{g_subs}"
                else:
                    stats = f"T: {_format_seconds(g_time)}, G:{g_subs}"
                lines.append(
                    f"  {GAME_DISPLAY[game]}: "
                    f"{gl.player_name} ({_pts(gl.total_points)}, {stats})"
                )

    # Prizes
    lb = weekly_leaderboard(week_filtered)
    prizes = prize_allocations(lb)
    prize_lines: List[str] = []
    if prizes.most_firsts is not None:
        firsts = prizes.most_firsts.first_places
        firsts_word = "1 first" if firsts == 1 else f"{firsts} firsts"
        prize_lines.append(
            f"  Most firsts: {prizes.most_firsts.player_name} ({firsts_word})"
        )
    if prizes.most_lasts is not None:
        lasts = prizes.most_lasts.last_places
        lasts_word = "1 last" if lasts == 1 else f"{lasts} lasts"
        prize_lines.append(
            f"  Most lasts: {prizes.most_lasts.player_name} ({lasts_word})"
        )
    if prizes.best_average is not None:
        ba = prizes.best_average
        prize_lines.append(
            f"  Best average: {ba.player_name} "
            f"(avg {ba.average_points:.1f} pts/game, {ba.submissions} submissions)"
        )
    if prizes.fastest_total_time is not None:
        ft = prizes.fastest_total_time
        prize_lines.append(
            f"  Fastest total time: {ft.player_name} "
            f"({_format_seconds(ft.total_time)} across "
            f"{ft.time_based_submissions} rounds)"
        )
    if prize_lines:
        lines.append("")
        lines.append("Prizes:")
        lines.extend(prize_lines)

    # Month / year totals — appended when the wrap's week_end also
    # closes out the month / year. Caller-driven (pass scores or
    # don't), mirrors daily_recap's hook.
    month_block = _period_totals_block(
        month_scores,
        enabled_games,
        f"Month totals — {week_end.strftime('%b %Y')}",
    )
    if month_block:
        lines.append("")
        lines.extend(month_block)

    year_block = _period_totals_block(
        year_scores, enabled_games, f"Year totals — {week_end.year}"
    )
    if year_block:
        lines.append("")
        lines.extend(year_block)

    return "\n".join(lines).rstrip() + "\n"
