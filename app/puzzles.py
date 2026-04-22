"""LinkedIn game puzzle-number epoch and current-puzzle math.

LinkedIn releases a new puzzle for each game daily at **midnight US
Pacific time** (honouring US daylight saving). The previous day's puzzle
expires at the same moment. Because the flip is tied to LA time — not
the submitter's local time — a submission at 4:30pm Sydney (still
yesterday in LA) is for *yesterday's* puzzle, while one at 5:30pm Sydney
(just past midnight in LA) is for *today's*. Exactly the same puzzle
number is live for every player worldwide in that window.

:data:`PUZZLE_EPOCH` pins, for each game, the puzzle number that was
live on a known LA date. Adding the LA-day delta from that reference
gives the number LinkedIn is currently serving. :func:`expected_puzzle_no`
is the small helper the webhook uses to reject stale/future submissions
so the leaderboard only ever contains today's scores.

To advance the epoch (e.g. if LinkedIn skips a number): update the tuple
for that game — no other code change required.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, Tuple
from zoneinfo import ZoneInfo

LA = ZoneInfo("America/Los_Angeles")


# (reference LA date, puzzle number live on that date).
# Captured 2026-04-22 PT. Bumped +1 across the board on confirmation
# that the initial values were off by one day — LinkedIn's actual
# rollover had already happened once by the time we pinned.
PUZZLE_EPOCH: Dict[str, Tuple[date, int]] = {
    "queens":      (date(2026, 4, 22), 722),
    "tango":       (date(2026, 4, 22), 562),
    "pinpoint":    (date(2026, 4, 22), 722),
    "crossclimb":  (date(2026, 4, 22), 722),
    "zip":         (date(2026, 4, 22), 401),
    "patches":     (date(2026, 4, 22), 36),
    "mini_sudoku": (date(2026, 4, 22), 254),
}


def la_date(ts: datetime) -> date:
    """Return the LA calendar date at ``ts``.

    ``ts`` must be timezone-aware. The webhook always builds ``now`` from
    :attr:`app.config.Settings.tz` (Sydney by default), so the conversion
    handles US daylight-saving transitions without ceremony.
    """
    return ts.astimezone(LA).date()


def expected_puzzle_no(game: str, now: datetime) -> int:
    """Return the puzzle number LinkedIn is currently serving for ``game``.

    Raises :class:`KeyError` for unknown games (caller should pre-filter
    with :data:`app.parsers.GAMES`).
    """
    ref_date, ref_no = PUZZLE_EPOCH[game]
    return ref_no + (la_date(now) - ref_date).days


def week_bounds(day: date) -> Tuple[date, date]:
    """Return ``(Monday, Sunday)`` for the week containing ``day``.

    Weeks anchor on Monday in LinkedIn's LA calendar to match the
    puzzle day; callers should pass an LA-anchored ``day`` (usually
    from :func:`la_date`). The function itself is pure calendar math
    — no timezone awareness — so it works for any ``date`` input.
    """
    monday = day - timedelta(days=day.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday
