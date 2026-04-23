"""Event-driven personal DMs fired from the webhook after an insert.

Kept separate from :mod:`app.jobs` (which handles cron-driven
broadcasts) because the shape and failure mode is different: these
are single-recipient messages fired synchronously in response to
something a player just did, and any failure must not block the
acknowledgment of a valid submission.

Public entry points:

- :func:`maybe_notify_personal_best` — compares a just-submitted raw
  score against the player's history for that game and DMs a
  congrats (new/tied PB) or a roast (new/tied worst-ever).
- :func:`maybe_notify_day_complete` — when the submission means the
  player has now played every enabled game for the LA day, DM a
  personal summary with per-game score + rank and their current
  weekly standing.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Dict, FrozenSet, List, Optional, Sequence

from .config import Settings
from .db import Player, Repository, ScoreRow
from .parsers import GAME_DISPLAY, GAME_DISPLAY_ORDER, format_raw_score
from .puzzles import week_bounds
from .scoring import assign_daily_points, weekly_leaderboard
from .sender import send_dm

logger = logging.getLogger(__name__)


# Rotated deterministically on raw_score so repeat PBs don't always
# get the same line. No emojis — matches the project's no-emoji
# convention. Keep short: WhatsApp DMs read better as one punchy line.
_NEW_PB_TEMPLATES = (
    "PERSONAL BEST on {game}: {new}. Previous best {prior}. Absolute scenes.",
    "New PB, {name}. {game} in {new} (was {prior}). Frame it.",
    "{name} just cooked {game}: {new}. Old PB {prior}. Victory lap allowed.",
    "Big dog alert: {name} clocked a new {game} PB of {new} (was {prior}).",
)
_TIED_PB_TEMPLATES = (
    "Matched your {game} PB ({new}). Consistency is a skill.",
    "Tied your {game} best ({new}). Annoyingly reliable.",
    "{name}, you equalled your own {game} record ({new}). Do it again and it's a trend.",
)
_NEW_WORST_TEMPLATES = (
    "Yikes. Worst-ever {game} ({new}). Previous low was {prior}. Wasn't your day.",
    "{name} put up a new personal-worst {game}: {new}. The old shame was {prior}.",
    "Oof. {game} in {new} — your new bottom of the barrel (was {prior}). We've all been there. Some of us more than others.",
)
_TIED_WORST_TEMPLATES = (
    "Tied your worst-ever {game} ({new}). At least you're predictable.",
    "{name}, you equalled your personal-worst {game} ({new}). Consistency is still technically a skill.",
)


def _pick(templates: tuple, key: int) -> str:
    """Deterministic template selection so reruns are idempotent."""
    return templates[key % len(templates)]


def _classify(
    prior_raws: list[int], new_raw: int
) -> Optional[str]:
    """Return ``"new_pb"`` / ``"tied_pb"`` / ``"new_worst"`` /
    ``"tied_worst"`` based on how ``new_raw`` compares to
    ``prior_raws`` (the player's other submissions for the same game,
    excluding the just-inserted row). Returns ``None`` when the new
    score is middling or there's no history to compare against.

    Lower raw_score always means "better" — seconds for time games,
    guess count for pinpoint — so the direction is the same for both.
    """
    if not prior_raws:
        return None
    best_before = min(prior_raws)
    worst_before = max(prior_raws)
    if new_raw < best_before:
        return "new_pb"
    if new_raw == best_before:
        # Guard against the degenerate case where best == worst
        # (all prior scores identical): treat it as a tied PB so
        # we lean positive rather than mocking a consistent player.
        return "tied_pb"
    if new_raw > worst_before:
        return "new_worst"
    if new_raw == worst_before:
        return "tied_worst"
    return None


def render_personal_best_message(
    *,
    player_name: str,
    game: str,
    new_raw: int,
    prior_raws: list[int],
) -> Optional[str]:
    """Pure-function core of :func:`maybe_notify_personal_best` —
    returns the DM body to send, or ``None`` if the score isn't
    notable. Exposed separately so tests can assert on the text
    without stubbing the Twilio sender."""
    verdict = _classify(prior_raws, new_raw)
    if verdict is None:
        return None

    game_label = GAME_DISPLAY.get(game, game)
    new_str = format_raw_score(game, new_raw)

    if verdict == "new_pb":
        prior_str = format_raw_score(game, min(prior_raws))
        return _pick(_NEW_PB_TEMPLATES, new_raw).format(
            name=player_name, game=game_label, new=new_str, prior=prior_str
        )
    if verdict == "tied_pb":
        return _pick(_TIED_PB_TEMPLATES, new_raw).format(
            name=player_name, game=game_label, new=new_str
        )
    if verdict == "new_worst":
        prior_str = format_raw_score(game, max(prior_raws))
        return _pick(_NEW_WORST_TEMPLATES, new_raw).format(
            name=player_name, game=game_label, new=new_str, prior=prior_str
        )
    # tied_worst
    return _pick(_TIED_WORST_TEMPLATES, new_raw).format(
        name=player_name, game=game_label, new=new_str
    )


def maybe_notify_personal_best(
    repo: Repository,
    settings: Optional[Settings],
    *,
    player: Player,
    game: str,
    new_raw: int,
) -> Optional[str]:
    """DM ``player`` a congrats or roast if ``new_raw`` is a new/tied
    best or worst for this game. Returns the DM body sent, or
    ``None`` when the score isn't notable (or ``settings`` is
    ``None``, which is the in-webhook fallback when Twilio isn't
    configured — tests exercise this path).

    Exceptions are caught and logged so a notification failure can't
    sink the webhook reply to the original submission.
    """
    try:
        all_scores = repo.list_player_scores(player.id)
    except Exception:
        logger.exception(
            "Failed to load player history for PB check (player=%s)", player.id
        )
        return None

    # Exclude exactly one occurrence of the just-inserted row so the
    # comparison is "this submission vs. every prior submission for
    # this game". If the player resubmits a duplicate raw_score the
    # DB layer dedupes it; we only hit this path on a fresh insert.
    game_raws = [s.raw_score for s in all_scores if s.game == game]
    try:
        game_raws.remove(new_raw)
    except ValueError:
        # Shouldn't happen (we were just inserted) — skip notification
        # rather than risk a false PB on inconsistent state.
        logger.warning(
            "Just-inserted %s %s not found in player %s history — "
            "skipping PB check",
            game,
            new_raw,
            player.id,
        )
        return None

    body = render_personal_best_message(
        player_name=player.display_name,
        game=game,
        new_raw=new_raw,
        prior_raws=game_raws,
    )
    if body is None:
        return None

    if settings is None:
        # Local / test path where Twilio isn't configured. Still
        # return the body so the caller can log / test it.
        logger.info("PB DM (dry-run, no settings): %s", body)
        return body

    try:
        send_dm(settings, player.whatsapp_id, body)
    except Exception:
        logger.exception(
            "Failed to send PB DM to player %s (%s)",
            player.id,
            player.whatsapp_id,
        )
    return body


# ---------------------------------------------------------------------------
# maybe_notify_day_complete — "you're done for today" personal summary
# ---------------------------------------------------------------------------


def _format_points(pts: float) -> str:
    """Match the scheduler's ``_pts`` convention: integer-valued
    floats stay clean ("5 pts"), fractional values render as N.N."""
    if abs(pts - round(pts)) < 1e-9:
        p = int(round(pts))
        return "1 pt" if p == 1 else f"{p} pts"
    return f"{pts:.1f} pts"


def _rank_among(pid: int, ranking: Sequence[int]) -> int:
    """Return 1-based rank of ``pid`` inside ``ranking`` (assumed
    already sorted in rank order). Missing → ``len(ranking)`` (i.e.
    treats an unknown player as last-placed, which shouldn't happen
    in practice since the caller only invokes this when the player
    has a submission in the group)."""
    for i, x in enumerate(ranking, start=1):
        if x == pid:
            return i
    return len(ranking)


def render_day_complete_summary(
    *,
    player: Player,
    today: date,
    today_scores: Sequence[ScoreRow],
    week_scores: Sequence[ScoreRow],
    enabled_games: FrozenSet[str],
) -> str:
    """Build the personal "day done" summary body.

    Pure function — caller fetches scores and decides whether to
    send. Per-game rankings are computed from ``today_scores`` using
    :func:`assign_daily_points` (the same helper the daily recap
    uses, so points match). Weekly standing is computed from
    ``week_scores`` via :func:`weekly_leaderboard`.
    """
    # Group today's scores by (game, puzzle_no) so per-game points
    # match exactly what the group recap will show.
    by_group: Dict[tuple, List[ScoreRow]] = {}
    for s in today_scores:
        if s.game not in enabled_games:
            continue
        by_group.setdefault((s.game, s.puzzle_no), []).append(s)

    lines: List[str] = [
        f"Day done, {player.display_name}! "
        f"Your {today.strftime('%a %d %b')} scorecard:",
        "",
    ]

    total_today = 0.0
    for game in GAME_DISPLAY_ORDER:
        if game not in enabled_games:
            continue
        # Find the group this player appeared in for this game.
        matching_keys = [
            k for k in by_group
            if k[0] == game and any(s.player_id == player.id for s in by_group[k])
        ]
        if not matching_keys:
            continue  # shouldn't happen when the caller has verified
                      # completion, but belt-and-braces
        key = matching_keys[0]
        group = sorted(by_group[key], key=lambda s: s.raw_score)
        pts_map = assign_daily_points(group)
        my = next(s for s in group if s.player_id == player.id)
        pts = pts_map[player.id]
        total_today += pts
        # Rank among group by raw_score asc (lower is better across
        # all games — seconds or guess count).
        rank = 1
        for s in group:
            if s.raw_score < my.raw_score:
                rank += 1
        lines.append(
            f"  {GAME_DISPLAY[game]}: "
            f"{format_raw_score(game, my.raw_score)} "
            f"(rank {rank}/{len(group)}, {_format_points(pts)})"
        )

    lines.append("")
    lines.append(f"Today's total: {_format_points(total_today)}.")

    # Weekly standing.
    lb = weekly_leaderboard(list(week_scores))
    if lb:
        ids = [p.player_id for p in lb]
        week_rank = _rank_among(player.id, ids)
        me = next((p for p in lb if p.player_id == player.id), None)
        if me is not None:
            lines.append(
                f"Week: {_format_points(me.total_points)}, "
                f"currently {week_rank}/{len(lb)}."
            )

    return "\n".join(lines)


def maybe_notify_day_complete(
    repo: Repository,
    settings: Optional[Settings],
    *,
    player: Player,
    today: date,
    enabled_games: FrozenSet[str],
) -> Optional[str]:
    """DM ``player`` a summary when this submission means they've now
    played every enabled game for ``today``. Returns the DM body
    sent, or ``None`` when the player isn't done yet.

    Idempotent-in-practice: "has every game" only becomes true once
    per day per player (further submissions for an already-done game
    bounce off the uniqueness constraint). Callers still wrap the
    invocation in try/except so a transient DB hiccup can't mask the
    webhook ack.
    """
    if not enabled_games:
        return None

    today_scores = repo.list_scores(date_from=today, date_to=today)
    played = {
        s.game
        for s in today_scores
        if s.player_id == player.id and s.game in enabled_games
    }
    if played < set(enabled_games):
        return None  # still outstanding games, not done yet

    # Pull the full week so the summary can render the player's
    # running weekly standing alongside today's breakdown.
    monday, sunday = week_bounds(today)
    week_scores = repo.list_scores(date_from=monday, date_to=sunday)

    body = render_day_complete_summary(
        player=player,
        today=today,
        today_scores=today_scores,
        week_scores=week_scores,
        enabled_games=enabled_games,
    )

    if settings is None:
        logger.info("Day-complete DM (dry-run): %s", body)
        return body

    try:
        send_dm(settings, player.whatsapp_id, body)
    except Exception:
        logger.exception(
            "Failed to send day-complete DM to player %s (%s)",
            player.id,
            player.whatsapp_id,
        )
    return body
