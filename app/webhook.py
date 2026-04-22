"""Webhook business logic — pure and fully testable without FastAPI.

:func:`handle_inbound` accepts the three Twilio fields we care about plus
an injected :class:`~app.db.Repository` and returns the bot's reply text.
It performs no I/O of its own beyond whatever the repository does.

Commands (case-insensitive):
- ``stats`` — reply with the sender's all-time per-game stats.
- ``unparsed`` — reply with the last 10 unparsed messages (admin debug).
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, FrozenSet, List, Optional, Set

from .db import Repository, ScoreRow
from .parsers import GAMES, looks_like_score, parse_any

_GAME_DISPLAY = {
    "queens": "Queens",
    "tango": "Tango",
    "pinpoint": "Pinpoint",
    "crossclimb": "Crossclimb",
    "zip": "Zip",
    "patches": "Patches",
    "mini_sudoku": "Mini Sudoku",
}

_GAME_ORDER = (
    "queens",
    "tango",
    "crossclimb",
    "zip",
    "pinpoint",
    "patches",
    "mini_sudoku",
)


def _format_score(game: str, raw_score: int) -> str:
    if game == "pinpoint":
        noun = "guess" if raw_score == 1 else "guesses"
        return f"{raw_score} {noun}"
    minutes, seconds = divmod(raw_score, 60)
    return f"{minutes}:{seconds:02d}"


def _help_text() -> str:
    return (
        "Hi! Send me your LinkedIn game share text (Queens, Tango, "
        "Pinpoint, Crossclimb, Zip, Patches, or Mini Sudoku) and I'll "
        "track it for the weekly leaderboard.\n\n"
        "Commands: stats, unparsed"
    )


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
    for game in _GAME_ORDER:
        if game in best:
            lines.append(
                f"  {_GAME_DISPLAY[game]}: "
                f"{_format_score(game, best[game])} "
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


def handle_inbound(
    repo: Repository,
    *,
    from_: str,
    body: str,
    profile_name: str,
    now: datetime,
    enabled_games: FrozenSet[str] = frozenset(GAMES),
    expected_puzzle_no: Optional[Callable[[str, datetime], int]] = None,
) -> str:
    """Process an inbound WhatsApp message. Returns the bot's reply text.

    ``expected_puzzle_no``, if provided, is called as
    ``expected_puzzle_no(game, now)`` and returns the puzzle number
    LinkedIn is currently serving for that game. When the submitted
    ``puzzle_no`` doesn't match, the handler rejects the submission with
    a helpful message explaining the LA-midnight rollover. Passing
    ``None`` (the default) skips validation — used by unit tests so they
    can exercise the handler with arbitrary puzzle numbers.
    """
    body_stripped = (body or "").strip()
    if not body_stripped:
        return _help_text()

    # Check for commands before attempting score parsing
    lower = body_stripped.lower()
    if lower == "stats":
        return _handle_stats(repo, from_, profile_name)
    if lower == "unparsed":
        return _handle_unparsed(repo)

    # Try to parse as a game share
    parsed = parse_any(body_stripped)

    if parsed is None:
        if looks_like_score(body_stripped):
            repo.log_unparsed(from_, body_stripped)
            return (
                "That looks like a LinkedIn game share but I couldn't parse "
                "it. I've logged the message so we can tune the format."
            )
        return _help_text()

    pretty_game = _GAME_DISPLAY[parsed.game]

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
    puzzle_date = now.date()

    inserted = repo.insert_score(
        player_id=player.id,
        game=parsed.game,
        puzzle_no=parsed.puzzle_no,
        puzzle_date=puzzle_date,
        raw_score=parsed.raw_score,
        share_text=parsed.share_text,
    )

    pretty_new = _format_score(parsed.game, parsed.raw_score)

    if not inserted:
        existing_raw = repo.get_existing_score(
            player_id=player.id,
            game=parsed.game,
            puzzle_no=parsed.puzzle_no,
        )
        if existing_raw is not None:
            pretty_existing = _format_score(parsed.game, existing_raw)
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
