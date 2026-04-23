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
"""

from __future__ import annotations

import logging
from typing import Optional

from .config import Settings
from .db import Player, Repository
from .parsers import GAME_DISPLAY, format_raw_score
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
