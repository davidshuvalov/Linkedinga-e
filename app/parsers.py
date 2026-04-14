"""LinkedIn game share-text parsers.

Each parser returns a :class:`ParsedScore` or ``None``. :func:`parse_any`
dispatches across all known games and returns the first hit.

``raw_score`` convention:

- ``queens``, ``tango``, ``crossclimb``, ``zip`` → duration in **seconds**
  (lower is better).
- ``pinpoint`` → number of guesses used, 1–5 (lower is better).

The regexes are tuned against real share-text samples captured 2026-04.
The time-based games currently put the score on its own second line
(e.g. ``Queens #714\\n0:10 👑``), while pinpoint keeps ``X guesses`` on the
same line as the puzzle number. See :class:`TestRealSamples` in
``tests/test_parsers.py`` for the verbatim fixtures used as regression
tests — update both together if LinkedIn ever changes the format again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, Optional

GAMES = ("queens", "tango", "pinpoint", "crossclimb", "zip")


@dataclass(frozen=True)
class ParsedScore:
    """A successfully parsed share-text.

    Attributes:
        game: One of ``GAMES``.
        puzzle_no: Daily puzzle number as printed in the share text (``#N``).
        raw_score: Seconds for time-based games, or guess count for pinpoint.
        share_text: The original message body, preserved verbatim for audit.
    """

    game: str
    puzzle_no: int
    raw_score: int
    share_text: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIME_PATTERN = r"(\d{1,2}:\d{2}(?::\d{2})?)"
_NUM_PATTERN = r"\s*#\s*(\d+)"
# Filler allowed between the puzzle ``#N`` and the score.
#
# The time-based games currently render the time on its own line, so we
# allow any non-digit characters (including newlines) between the puzzle
# number and the first ``M:SS`` we find. ``[^\d]`` (rather than ``.*``) is
# important: because the filler can't contain digits, a chatty message
# like "Queens failed, tried Zip #1 | 0:30" still can't be mis-attributed
# to Queens — the engine can't skip over Zip's ``1`` to reach the time.
_TIME_GAME_FILLER = r"[^\d]*?"
# Pinpoint keeps "X guesses" on the same line as ``#N``, so we restrict
# its filler to non-newline. This also prevents us from accidentally
# matching the ASCII digit inside keycap emoji like ``1️⃣`` on the rows
# underneath the header.
_PINPOINT_FILLER = r"[^\d\n]*?"


def _time_to_seconds(time_str: str) -> Optional[int]:
    """Convert ``M:SS`` / ``H:MM:SS`` / plain seconds into total seconds.

    Returns ``None`` for anything that doesn't look numeric.
    """
    time_str = time_str.strip()
    if not time_str:
        return None
    try:
        parts = [int(p) for p in time_str.split(":")]
    except ValueError:
        return None
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def _match_time_based(game: str, text: str) -> Optional[ParsedScore]:
    """Shared implementation for the four time-based games."""
    pattern = rf"\b{game}\b{_NUM_PATTERN}{_TIME_GAME_FILLER}{_TIME_PATTERN}"
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    seconds = _time_to_seconds(m.group(2))
    if seconds is None:
        return None
    return ParsedScore(
        game=game,
        puzzle_no=int(m.group(1)),
        raw_score=seconds,
        share_text=text,
    )


# ---------------------------------------------------------------------------
# Per-game parsers
# ---------------------------------------------------------------------------


def parse_queens(text: str) -> Optional[ParsedScore]:
    """Parse a Queens share.

    Real share-text shape (verified 2026-04)::

        Queens #714
        0:10 👑
        lnkd.in/queens.
    """
    return _match_time_based("queens", text)


def parse_tango(text: str) -> Optional[ParsedScore]:
    """Parse a Tango share.

    Real share-text shape (verified 2026-04)::

        Tango #554
        0:35 🌗
        lnkd.in/tango.
    """
    return _match_time_based("tango", text)


def parse_crossclimb(text: str) -> Optional[ParsedScore]:
    """Parse a Crossclimb share.

    Real share-text shape (verified 2026-04)::

        Crossclimb #714
        1:44 🪜
        lnkd.in/crossclimb.
    """
    return _match_time_based("crossclimb", text)


def parse_zip(text: str) -> Optional[ParsedScore]:
    """Parse a Zip share.

    Real share-text shape (verified 2026-04)::

        Zip #393
        0:09 🏁
        lnkd.in/zip.
    """
    return _match_time_based("zip", text)


def parse_pinpoint(text: str) -> Optional[ParsedScore]:
    """Parse a Pinpoint share.

    Real share-text shape (verified 2026-04)::

        Pinpoint #714 | 4 guesses
        1️⃣  | 2% match
        2️⃣  | 9% match
        3️⃣  | 3% match
        4️⃣  | 100% match 📌
        lnkd.in/pinpoint.
    """
    pattern = rf"\bpinpoint\b{_NUM_PATTERN}{_PINPOINT_FILLER}(\d+)\s*guess"
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    guesses = int(m.group(2))
    if not 1 <= guesses <= 5:
        return None
    return ParsedScore(
        game="pinpoint",
        puzzle_no=int(m.group(1)),
        raw_score=guesses,
        share_text=text,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_PARSERS: Dict[str, Callable[[str], Optional[ParsedScore]]] = {
    "queens": parse_queens,
    "tango": parse_tango,
    "pinpoint": parse_pinpoint,
    "crossclimb": parse_crossclimb,
    "zip": parse_zip,
}


def looks_like_score(text: str) -> bool:
    """Rough heuristic: does this look like any LinkedIn game share?

    Used by the webhook to distinguish score-ish messages that fail to parse
    (which should be logged to ``unparsed_messages``) from unrelated chatter.
    """
    lower = text.lower()
    if "lnkd.in/" in lower:
        return True
    return any(re.search(rf"\b{g}\b", lower) for g in GAMES)


def parse_any(text: str) -> Optional[ParsedScore]:
    """Try every parser and return the first successful :class:`ParsedScore`.

    Returns ``None`` if no parser matched.
    """
    if not text:
        return None
    for parser in _PARSERS.values():
        parsed = parser(text)
        if parsed is not None:
            return parsed
    return None
