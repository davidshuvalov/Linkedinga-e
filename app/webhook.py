"""Webhook business logic — pure and fully testable without FastAPI.

:func:`handle_inbound` accepts the three Twilio fields we care about plus
an injected :class:`~app.db.Repository` and returns the bot's reply text.
It performs no I/O of its own beyond whatever the repository does.
"""

from __future__ import annotations

from datetime import datetime

from .db import Repository
from .parsers import looks_like_score, parse_any

_GAME_DISPLAY = {
    "queens": "Queens",
    "tango": "Tango",
    "pinpoint": "Pinpoint",
    "crossclimb": "Crossclimb",
    "zip": "Zip",
}


def _format_score(game: str, raw_score: int) -> str:
    """Render ``raw_score`` for a human: ``M:SS`` for time games, ``N guess(es)``
    for pinpoint."""
    if game == "pinpoint":
        noun = "guess" if raw_score == 1 else "guesses"
        return f"{raw_score} {noun}"
    minutes, seconds = divmod(raw_score, 60)
    return f"{minutes}:{seconds:02d}"


def _help_text() -> str:
    return (
        "Hi! Send me your LinkedIn game share text (Queens, Tango, "
        "Pinpoint, Crossclimb, or Zip) and I'll track it for the weekly "
        "leaderboard."
    )


def handle_inbound(
    repo: Repository,
    *,
    from_: str,
    body: str,
    profile_name: str,
    now: datetime,
) -> str:
    """Process an inbound WhatsApp message. Returns the bot's reply text.

    Args:
        repo: Data-access repository.
        from_: Twilio ``From`` field, e.g. ``whatsapp:+61400000001``.
        body: Twilio ``Body`` — the raw message text.
        profile_name: Twilio ``ProfileName`` — sender's WhatsApp display name
            (may be empty if the sender hasn't set one).
        now: Current time in the app timezone. Used to derive ``puzzle_date``.
    """
    body_stripped = (body or "").strip()
    if not body_stripped:
        return _help_text()

    parsed = parse_any(body_stripped)

    if parsed is None:
        if looks_like_score(body_stripped):
            repo.log_unparsed(from_, body_stripped)
            return (
                "That looks like a LinkedIn game share but I couldn't parse "
                "it. I've logged the message so we can tune the format."
            )
        return _help_text()

    display_name = (profile_name or "").strip() or from_

    player = repo.get_or_create_player(from_, display_name)
    puzzle_date = now.date()

    inserted = repo.insert_score(
        player_id=player.id,
        game=parsed.game,
        puzzle_no=parsed.puzzle_no,
        puzzle_date=puzzle_date,
        raw_score=parsed.raw_score,
        share_text=parsed.share_text,
    )

    pretty_game = _GAME_DISPLAY[parsed.game]
    pretty_score = _format_score(parsed.game, parsed.raw_score)

    if not inserted:
        return (
            f"You already submitted {pretty_game} #{parsed.puzzle_no} "
            f"({pretty_score}). Ignoring the duplicate."
        )

    return (
        f"Got it, {player.display_name}. "
        f"{pretty_game} #{parsed.puzzle_no}: {pretty_score}."
    )
