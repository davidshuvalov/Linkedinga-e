"""LinkedIn game share-text parsers.

Each parser returns a :class:`ParsedScore` or ``None``. :func:`parse_any`
dispatches across all known games and returns the first hit.

``raw_score`` convention:

- ``queens``, ``tango``, ``crossclimb``, ``zip``, ``patches``,
  ``mini_sudoku``, ``wend`` → duration in **seconds** (lower is better).
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

GAMES = (
    "queens",
    "tango",
    "pinpoint",
    "crossclimb",
    "zip",
    "patches",
    "mini_sudoku",
    "wend",
)

# Single source of truth for how each game name is rendered in user-facing
# messages (recaps, stats, rejection replies). Keep here alongside
# :data:`GAMES` so there's one canonical list of games + display names.
GAME_DISPLAY = {
    "queens": "Queens",
    "tango": "Tango",
    "pinpoint": "Pinpoint",
    "crossclimb": "Crossclimb",
    "zip": "Zip",
    "patches": "Patches",
    "mini_sudoku": "Mini Sudoku",
    "wend": "Wend",
}

# Preferred order for rendering per-game sections in recaps and stats.
# Intentionally different from :data:`GAMES` (which is the parser dispatch
# order): puts the time-based games first, then pinpoint, then the newer
# games at the end so the recap reads predictably.
GAME_DISPLAY_ORDER = (
    "queens",
    "tango",
    "crossclimb",
    "zip",
    "pinpoint",
    "patches",
    "mini_sudoku",
    "wend",
)


def format_raw_score(game: str, raw: int) -> str:
    """Render a stored ``raw_score`` as the human string a user would see.

    - Pinpoint scores are guess counts (1–5): "``N guess``" or
      "``N guesses``".
    - Every other game stores seconds: "``M:SS``".

    Shared between the webhook reply builder and the recap/wrap
    formatters so both surfaces emit identical strings.
    """
    if game == "pinpoint":
        noun = "guess" if raw == 1 else "guesses"
        return f"{raw} {noun}"
    minutes, seconds = divmod(raw, 60)
    return f"{minutes}:{seconds:02d}"

# How to locate each game's name inside free-text. Single-word games use a
# plain ``\b``-bounded match; ``mini_sudoku`` has a space in the display
# name so we use ``\s+`` between the tokens.
_GAME_NAME_PATTERNS: Dict[str, str] = {
    "queens": r"\bqueens\b",
    "tango": r"\btango\b",
    "pinpoint": r"\bpinpoint\b",
    "crossclimb": r"\bcrossclimb\b",
    "zip": r"\bzip\b",
    "patches": r"\bpatches\b",
    "mini_sudoku": r"\bmini\s+sudoku\b",
    "wend": r"\bwend\b",
}


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
    """Shared implementation for the six time-based games."""
    name_pattern = _GAME_NAME_PATTERNS[game]
    pattern = rf"{name_pattern}{_NUM_PATTERN}{_TIME_GAME_FILLER}{_TIME_PATTERN}"
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


def parse_patches(text: str) -> Optional[ParsedScore]:
    """Parse a Patches share.

    Real share-text shape (verified 2026-04)::

        Patches #28 | 0:13 🧶
        With no hints
        lnkd.in/patches.
    """
    return _match_time_based("patches", text)


def parse_mini_sudoku(text: str) -> Optional[ParsedScore]:
    """Parse a Mini Sudoku share.

    Real share-text shape (verified 2026-04)::

        Mini Sudoku #246 | 1:16 ✏️
        The classic game, made mini. Handcrafted by the originators of
        "Sudoku."
        lnkd.in/minisudoku.
    """
    return _match_time_based("mini_sudoku", text)


def parse_wend(text: str) -> Optional[ParsedScore]:
    """Parse a Wend share.

    Real share-text shape::

        Wend #N
        M:SS 🧶
        lnkd.in/wend.
    """
    return _match_time_based("wend", text)


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
    name_pattern = _GAME_NAME_PATTERNS["pinpoint"]
    pattern = rf"{name_pattern}{_NUM_PATTERN}{_PINPOINT_FILLER}(\d+)\s*guess"
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
    "patches": parse_patches,
    "mini_sudoku": parse_mini_sudoku,
    "wend": parse_wend,
}


def looks_like_score(text: str) -> bool:
    """Rough heuristic: does this look like an attempted LinkedIn game share?

    Used by the webhook to distinguish score-ish messages that fail to parse
    (which should be replied to + logged to ``unparsed_messages``) from
    unrelated chatter (which should be silently ignored so the bot doesn't
    spam a group chat).

    We treat a message as score-like only when it carries something a
    parser would genuinely try to match:

    - a LinkedIn game share URL (``lnkd.in/``), **or**
    - a game name *and* an ``#<number>`` puzzle marker.

    A bare mention of a game name (e.g. "Queens is fun today") is **not**
    enough — that's normal chat and should pass silently.
    """
    lower = text.lower()
    if "lnkd.in/" in lower:
        return True
    has_game_name = any(re.search(p, lower) for p in _GAME_NAME_PATTERNS.values())
    has_puzzle_hash = re.search(r"#\s*\d+", lower) is not None
    return has_game_name and has_puzzle_hash


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
