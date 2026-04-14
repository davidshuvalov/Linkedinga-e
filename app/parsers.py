"""LinkedIn game share-text parsers.

Each parser returns a :class:`ParsedScore` or ``None``. :func:`parse_any`
dispatches across all known games and returns the first hit.

``raw_score`` convention:

- ``queens``, ``tango``, ``crossclimb``, ``zip`` → duration in **seconds**
  (lower is better).
- ``pinpoint`` → number of guesses used, 1–5 (lower is better).

.. note::
   These regexes are **permissive first drafts** built from common LinkedIn
   share-text shapes. They intentionally ignore emoji, stray whitespace, and
   trailing ``lnkd.in/...`` links. Once we have real share-text samples from
   each of the five games, tighten them and add regression fixtures — see
   the ``TODO`` markers on each parser.
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
# Allow arbitrary whitespace (including newlines) between the game name and
# its "#N", but only non-digit/non-newline filler between the number and the
# time/guess-count — this keeps matches on the same line as the score so a
# chatty message like "Queens failed, tried Zip #1 | 0:30" doesn't get
# mis-attributed to Queens.
_NUM_PATTERN = r"\s*#\s*(\d+)"


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
    pattern = rf"\b{game}\b{_NUM_PATTERN}[^\d\n]*?{_TIME_PATTERN}"
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

    TODO: tune once a real share-text sample is available. Expected shape::

        Queens #365 | 1:23
        First solve of the day
        lnkd.in/queens
    """
    return _match_time_based("queens", text)


def parse_tango(text: str) -> Optional[ParsedScore]:
    """Parse a Tango share.

    TODO: tune once a real share-text sample is available. Expected shape::

        Tango #123 | 0:45 and flawless
        ☀️🌑🌑☀️
        lnkd.in/tango
    """
    return _match_time_based("tango", text)


def parse_crossclimb(text: str) -> Optional[ParsedScore]:
    """Parse a Crossclimb share.

    TODO: tune once a real share-text sample is available. Expected shape::

        Crossclimb #77 | 1:45
        🪜
        lnkd.in/crossclimb
    """
    return _match_time_based("crossclimb", text)


def parse_zip(text: str) -> Optional[ParsedScore]:
    """Parse a Zip share.

    TODO: tune once a real share-text sample is available. Expected shape::

        Zip #88 | 0:42 🏁
        With 0 backtracks
        lnkd.in/zip
    """
    return _match_time_based("zip", text)


def parse_pinpoint(text: str) -> Optional[ParsedScore]:
    """Parse a Pinpoint share.

    TODO: tune once a real share-text sample is available. Expected shape::

        Pinpoint #200 | 3 guesses
        📌 📌 ✅
        lnkd.in/pinpoint
    """
    pattern = rf"\bpinpoint\b{_NUM_PATTERN}[^\d\n]*?(\d+)\s*guess"
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
