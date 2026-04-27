"""Event-driven personal DMs fired from the webhook after an insert.

Kept separate from :mod:`app.jobs` (which handles cron-driven
broadcasts) because the shape and failure mode is different: these
are single-recipient messages fired synchronously in response to
something a player just did, and any failure must not block the
acknowledgment of a valid submission.

Public entry points:

- :func:`maybe_notify_personal_best` — runs every detector below
  against the just-inserted submission and DMs a single line drawn
  from whichever trigger fired. When more than one trigger fires
  (e.g. a personal best that's ALSO the best of today), one is
  picked at random — with one carve-out: a lifetime PB or worst
  trumps its weekday and year siblings, so a "fastest Queens ever"
  isn't competing with "fastest Friday Queens".

  Trigger kinds currently detected:
    * ``new_pb``        — beats the player's prior best for this game
    * ``tied_pb``       — equals the player's prior best
    * ``new_worst``     — beats the player's prior worst (slowest)
    * ``tied_worst``    — equals the player's prior worst
    * ``best_of_day``   — fastest score for this game today
                          across every player who's submitted
    * ``worst_of_day``  — slowest score for this game today
    * ``all_time_record``      — fastest score for this game ever
                                 (across every player, all time)
    * ``all_time_anti_record`` — slowest score for this game ever
                                 ("longest LinkedIn game ever")

- :func:`maybe_notify_day_complete` — when the submission means the
  player has now played every enabled game for the LA day, DM a
  personal summary with per-game score + rank and their current
  weekly standing.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from .config import Settings
from .db import Player, Repository, ScoreRow
from .parsers import GAME_DISPLAY, GAME_DISPLAY_ORDER, format_raw_score
from .puzzles import week_bounds
from .scoring import assign_daily_points, weekly_leaderboard
from .sender import send_dm

logger = logging.getLogger(__name__)


# Templates by trigger kind. Each pool gets randomly sampled when the
# trigger fires — no day-ordinal rotation here because submissions
# happen at any time and need to feel fresh per submission. WhatsApp
# DMs read best as one punchy line; keep them short.
_NEW_PB_TEMPLATES = (
    "PERSONAL BEST on {game}: {new}. Previous best {prior}. Absolute scenes.",
    "New PB, {name}. {game} in {new} (was {prior}). Frame it.",
    "{name} just cooked {game}: {new}. Old PB {prior}. Victory lap allowed.",
    "Big dog alert: {name} clocked a new {game} PB of {new} (was {prior}).",
    "{game} bowed to you, {name}. New PB {new}, smashed your old {prior}.",
    "Ding ding — new {game} PB for {name}: {new}. Yesterday's {prior} is now history.",
    "{name}, that's a fresh personal best on {game} ({new}). Old PB: {prior}. Yum.",
    "Stop the press — {name} hit {new} on {game} (PB, was {prior}).",
    "Cooking with gas, {name}. New {game} PB {new}, beating your own {prior}.",
    "{name} just rewrote their {game} record book. {new} (PB, was {prior}).",
    "Personal {game} record for {name}: {new}. Down from {prior}. Big day.",
    "{name}, a thing of beauty: {new} on {game}, your fastest ever (was {prior}).",
    "New PB unlocked, {name}. {game} in {new}. Old shame: {prior}.",
    "Tape measure out — {name} stretched their own {game} record to {new} (was {prior}).",
)
_TIED_PB_TEMPLATES = (
    "Matched your {game} PB ({new}). Consistency is a skill.",
    "Tied your {game} best ({new}). Annoyingly reliable.",
    "{name}, you equalled your own {game} record ({new}). Do it again and it's a trend.",
    "{name}, that's a PB tie on {game} ({new}). Inches from breaking it.",
    "Same as your {game} best, {name}: {new}. Steady as she goes.",
    "{name} matched their {game} PB ({new}). Twin peaks.",
    "Equal PB on {game}, {name}: {new}. Watch out for the next one.",
    "{name}, you tied your {game} record ({new}). The universe is consistent.",
    "Mirror match — {name}'s {game} in {new} ties their own personal best.",
    "{name}: {new} on {game}, dead level with your PB. Repeatable.",
)
_NEW_WORST_TEMPLATES = (
    "Yikes. Worst-ever {game} ({new}). Previous low was {prior}. Wasn't your day.",
    "{name} put up a new personal-worst {game}: {new}. The old shame was {prior}.",
    "Oof. {game} in {new} — your new bottom of the barrel (was {prior}). We've all been there. Some of us more than others.",
    "New basement, {name}. {game} in {new}. Old basement: {prior}. Renovation pending.",
    "{name}, that's your slowest-ever {game}: {new} (worst was {prior}). Brutal.",
    "Tough one, {name}. {game} in {new} — new personal worst (was {prior}).",
    "{name} just bottomed out on {game}: {new}. Old low: {prior}. Recovery starts now.",
    "Big sigh. {name}'s {game} in {new} — slowest you've ever managed (was {prior}).",
    "{name}, the bot is contractually obliged to mention: {new} on {game} is your new worst (was {prior}).",
    "Personal worst on {game}, {name}: {new}. Your old low was {prior}. We've all been there.",
    "{name} wrote a new chapter — \"{game} in {new}\". Old worst: {prior}. Ouch.",
    "{name}, {game} in {new} is officially your slowest. Beats {prior} for the wrong reason.",
    "{name}, the floor moved: {new} on {game} is your new bottom (was {prior}).",
    "Worst-ever {game} for {name}: {new}. The previous {prior} looks fast now.",
)
_TIED_WORST_TEMPLATES = (
    "Tied your worst-ever {game} ({new}). At least you're predictable.",
    "{name}, you equalled your personal-worst {game} ({new}). Consistency is still technically a skill.",
    "{name}, {new} on {game} — joint-worst with yourself. The dread tier.",
    "Match made in misery — {name}'s {game} in {new} ties your personal worst.",
    "Mirror-match, but the bad version. {name}'s {new} on {game} ties your worst.",
    "{name}, you tied your slowest-ever {game} ({new}). Reliable, just at the wrong end.",
    "Equal worst on {game} for {name}: {new}. The streak of disappointment continues.",
    "{name}, that's a tied personal worst on {game} ({new}). At least it can't get any worse. Probably.",
)

# "Best of today" — fastest score on this game for the current LA day
# across every player who's submitted. Implies a real beat, not a
# trivial first-of-the-day lonely-win. ``{margin}`` is filled in as
# a pretty time gap (e.g. "by 8 seconds" or "by 0:08") when there's
# a previous best to beat; templates that don't use {margin} are
# fine for the no-margin (first-to-beat) case.
_BEST_OF_DAY_TEMPLATES = (
    "{name}, that {game} run ({new}) is the fastest of the day. Top of the pile.",
    "Best {game} of the day so far, {name}: {new}. Currently uncatchable.",
    "{name} sets the {game} pace at {new}. Everyone else is now playing for second.",
    "{game} in {new} — fastest of the day, {name}. Pole position.",
    "{name}, you just leapfrogged the field on {game} ({new}). The board is yours for now.",
    "Sole proprietor of today's fastest {game}, {name}: {new}.",
    "{name}: {new} on {game}, fastest in the group today. Insufferable allowed.",
    "Today's {game} crown goes to {name}: {new}. Wear it loudly.",
    "{name} just clipped {game} in {new} — fastest of the day. Celebrate appropriately.",
    "Quick maths: {new} > everyone else's {game} today. {name}, you absolute legend.",
    "{name} drops {game} in {new} — currently the day's fastest. Hold steady.",
    "Number one on the {game} board today, {name}: {new}. Lap of honour begins.",
    "{name}: {new} on {game}, eclipsing the field. For now.",
    "Fastest {game} of the day stamped at {new}, {name}. Rest of the pack: catch up.",
    "{name}, you just bumped everyone off the top of {game} today: {new}.",
)

_WORST_OF_DAY_TEMPLATES = (
    "{name}, {new} on {game} is currently the slowest of the day. Floor is yours.",
    "Today's {game} wooden spoon: {name} with {new}. So far. There's still time. For others.",
    "{name} pulls up the rear on {game} today: {new}. Everyone else has set faster.",
    "Slowest {game} of the day belongs to {name}: {new}. Embrace the basement.",
    "{name}, your {game} in {new} is dead last on today's board. You did show up though.",
    "Bottom of today's {game} pile, {name}: {new}. The puzzle won that round.",
    "{name} clocks {game} in {new} — currently the day's worst. Could get worse if the day's not done.",
    "{game} in {new} — last of the day so far, {name}. Floor's not lava.",
    "{name}: {new} on {game}, dragging the average down today. We see you.",
    "Today's {game} basement: {name}, {new}. We're all friends here.",
    "{name}, the puzzle didn't blink: {new} on {game}, slowest of the day.",
    "Currently last on today's {game}, {name}: {new}. Tomorrow's a new day.",
)

# All-time record — fastest score for this game across all players,
# all time. Single message; no margin needed (the previous record is
# in {prior_holder} / {prior}).
_ALL_TIME_RECORD_TEMPLATES = (
    "{name} just set the ALL-TIME {game} record: {new}. {prior_holder}'s {prior} is now history.",
    "STOP THE SHOW. {name} hit {new} on {game} — fastest in this group's recorded history. Old record: {prior_holder}'s {prior}.",
    "Ladies and gentlemen, the new {game} world record holder is {name}: {new}. (Previous: {prior_holder}, {prior}.)",
    "{name} broke the {game} all-time record. {new} beats {prior_holder}'s {prior}. Plaque coming.",
    "Records exist to be broken, and {name} just broke the {game} one: {new}. {prior_holder}'s old {prior} is officially yesterday.",
    "{name}: {new} on {game}. New all-time record. The previous holder ({prior_holder}, {prior}) sends their regards.",
    "All-time {game} record falls to {name}: {new}. {prior_holder}'s {prior} held for as long as it could.",
    "{name} is the new {game} GOAT. {new}, beating {prior_holder}'s {prior}. Engrave it.",
    "History was made, {name}: {new} on {game} — the fastest this group has ever logged. {prior_holder}'s {prior} stood until now.",
    "The book is rewritten. {name} took {game}'s all-time record with {new}. (Was {prior_holder}, {prior}.)",
    "{name}, that's not just a PB — it's the {game} ALL-TIME record. {new}. Trophy room only just big enough.",
    "{name} just etched their name into the {game} hall of fame: {new}, eclipsing {prior_holder}'s {prior}.",
)

# All-time anti-record — slowest score for this game ever recorded.
# Lighter touch than a personal worst — this is rare and notable.
_ALL_TIME_ANTI_RECORD_TEMPLATES = (
    "{name}, that {game} run ({new}) is the slowest score this group has ever logged. Genuinely impressive.",
    "Notable achievement, {name}: {new} on {game} is the slowest ever recorded here. {prior_holder}'s {prior} is now legendary in a different way.",
    "{name} just claimed the all-time {game} anti-record: {new}. {prior_holder}'s {prior} has been usurped.",
    "Hall of mediocrity update, {name}: {new} on {game} is the slowest score this group has ever seen. (Previous low: {prior_holder}, {prior}.)",
    "{name}: {new}. That's the slowest {game} this bot has ever processed. {prior_holder}'s {prior} held the title until now.",
    "Brand new all-time worst for {game}: {name} with {new}. Beats {prior_holder}'s old {prior} for sheer endurance.",
    "{name}, that's officially the longest {game} anyone in this group has ever played: {new}. Take a bow. Or a nap.",
    "Records can go either way — {name}'s {new} on {game} is now the all-time slowest. ({prior_holder}, {prior}, finally relieved.)",
    "{name} just set a new bar — and lowered the floor. {game} in {new} is the group's all-time worst (was {prior_holder}, {prior}).",
    "{name}, {new} on {game} is the slowest score in this group's history. Unique distinction. Sort of.",
    "All-time anti-record alert: {name}'s {game} in {new} eclipses {prior_holder}'s {prior} for sheer footdragging.",
    "{name} just made history (the wrong kind): {new} on {game}, slowest ever in this group.",
)


# Phase D triggers — finer slices of personal history.
#   * dow_pb       — best ever on this game on this weekday
#                    (e.g. "fastest Tuesday Queens you've had")
#   * dow_worst    — worst ever on this weekday for this game
#   * year_pb      — fastest score for this game in the calendar year
#   * year_worst   — slowest score for this game in the calendar year
#   * first_today  — just-inserted is the only score for this game today
#                    ("you broke the seal" — solo player or first-mover)
# Format placeholders these pools support beyond the common
# {name}/{game}/{new}/{prior}: {weekday} (e.g. "Tuesday"),
# {year} (e.g. "2026").

_DOW_PB_TEMPLATES = (
    "{name}, that's your best-ever {game} on a {weekday}: {new} (was {prior}). "
    "Specific stats for specific people.",
    "Best {weekday} {game} of your career, {name}: {new}. Beats your old {weekday} record of {prior}.",
    "{name} owns {weekday}s on {game} now: {new} clear of your prior {prior}.",
    "{weekday} {name} just rewrote the personal {game} {weekday}-record: {new} beats {prior}.",
    "{name}, {weekday}s belong to you on {game}. {new} is your new best (was {prior}).",
    "Specific glory, {name}: {new} on {game} is your fastest-ever {weekday} (down from {prior}).",
    "Plot twist: {name}'s {weekday}s are now {game} highlight reels. {new} (was {prior}).",
    "{name} on a {weekday} on {game}: dangerous. {new}, beating your {prior}.",
    "Personal {weekday}-record fall — {name}, {game} in {new} (was {prior}). The week has a favourite day now.",
    "{name}, {game} on {weekday} is officially your thing now: {new} (was {prior}).",
    "Sneakily good, {name}. New {weekday} {game} PB: {new} (was {prior}).",
    "{name}, {weekday}s used to mean {prior} on {game}. Now they mean {new}. Update your CV.",
)

_DOW_WORST_TEMPLATES = (
    "{name}, {game} on a {weekday} just hit a new low: {new} (was {prior}). {weekday}s, hey.",
    "Worst {weekday} you've ever had on {game}, {name}: {new} (old low {prior}).",
    "{name}, that's a new {weekday}-worst for {game}: {new}. The {prior} from last {weekday} feels positively heroic now.",
    "{name}, {weekday} {game} bottoming out: {new} undercuts {prior}.",
    "Personal {weekday} anti-record on {game}, {name}: {new} (was {prior}). Tomorrow's a different weekday.",
    "{name}, on {weekday}s the {game} board now reads: 1) you, slowest. {new} (was {prior}).",
    "{name}, your {weekday}-worst for {game} just got worster: {new} (was {prior}).",
    "{game} on {weekday}s is your kryptonite, {name}: new low {new} (was {prior}).",
    "{name}, {new} on {game} is the slowest {weekday} you've ever played. (Old shame: {prior}.)",
    "Tough {weekday}, {name}. New {game} {weekday}-low: {new} (was {prior}).",
)

_YEAR_PB_TEMPLATES = (
    "{name}, that's your fastest {game} of {year}: {new} (was {prior}).",
    "Best {game} of the year so far, {name}: {new}. Old {year} best was {prior}.",
    "{name} sets a new {year} {game} PB: {new} (was {prior}). The year's still young, but for now you own it.",
    "Top of {name}'s {year} {game} chart: {new}, beating their old {prior}.",
    "{name}, {year} just got its {game} headline: {new} (was {prior}).",
    "Best {game} {name} has played this year: {new}. The old {year} mark was {prior}.",
    "{name}, that's a {year} PB on {game}: {new} (was {prior}). Bookmark it.",
    "{game} {year} record holder for {name}: {name}. {new} (was {prior}). Nepotism.",
    "Annual leaderboard moves: {name}'s best {game} of {year} is now {new} (was {prior}).",
    "{name} drops a {year} {game} best: {new}. Your old {year} record ({prior}) had a good run.",
    "Calendar's noticed, {name}: {new} on {game} is your {year} fastest (was {prior}).",
    "{name}, {game} {year} edition: {new}, your fastest of the year (was {prior}).",
)

_FIRST_TODAY_TEMPLATES = (
    "{name}, you broke the seal on today's {game}: {new}. The board's open.",
    "First on the board for today's {game}, {name}: {new}. Now the others have to catch up.",
    "{name}: {new} on {game} — you're first to play today. Set the bar wherever you fancy.",
    "{name}, you're the day's pioneer on {game}: {new}. Everyone else now has a number to beat.",
    "First in today, {name}: {new} on {game}. The day's {game} board has one entry, and it's yours.",
    "Today's {game} starts with {name}: {new}. First mover advantage.",
    "{name}, you opened today's {game} ledger at {new}. The rest of the field is on notice.",
    "Number one (so far) on today's {game}, {name}: {new}. Single-entry leaderboard. Enjoy it.",
    "{name} kicks today's {game} off at {new}. Everyone else is playing for second already.",
    "{name}, you're the early bird on today's {game}: {new}. Worm acquired.",
    "{name}, opening salvo on today's {game}: {new}. Lonely at the top, until it isn't.",
)


_TEMPLATES_BY_KIND: Dict[str, Tuple[str, ...]] = {
    "new_pb": _NEW_PB_TEMPLATES,
    "tied_pb": _TIED_PB_TEMPLATES,
    "new_worst": _NEW_WORST_TEMPLATES,
    "tied_worst": _TIED_WORST_TEMPLATES,
    "best_of_day": _BEST_OF_DAY_TEMPLATES,
    "worst_of_day": _WORST_OF_DAY_TEMPLATES,
    "all_time_record": _ALL_TIME_RECORD_TEMPLATES,
    "all_time_anti_record": _ALL_TIME_ANTI_RECORD_TEMPLATES,
    "dow_pb": _DOW_PB_TEMPLATES,
    "dow_worst": _DOW_WORST_TEMPLATES,
    "year_pb": _YEAR_PB_TEMPLATES,
    "first_today": _FIRST_TODAY_TEMPLATES,
}


# Trigger pick-time precedence: when a lifetime PB (or PB-tie) fires,
# the weekday-PB and year-PB triggers are subsumed by it — saying
# "fastest Friday Queens" alongside "fastest Queens ever" buries the
# lede. Same for the worst side. The trump map filters out the
# subordinate kinds whenever the dominant kind is in the candidate
# pool; the random pick then runs over the survivors.
_TRIGGER_TRUMPS: Dict[str, frozenset] = {
    "new_pb": frozenset({"dow_pb", "year_pb"}),
    "tied_pb": frozenset({"dow_pb", "year_pb"}),
    "new_worst": frozenset({"dow_worst"}),
    "tied_worst": frozenset({"dow_worst"}),
}


def _pick_trigger(triggers: Sequence["Trigger"]) -> "Trigger":
    """Choose one trigger from the candidate list. Lifetime PB / worst
    triggers (``new_pb``, ``tied_pb``, ``new_worst``, ``tied_worst``)
    subsume their narrower siblings (weekday / year variants) so a
    headline-worthy moment isn't diluted; among the survivors the
    pick is uniform random."""
    kinds = {t.kind for t in triggers}
    suppressed: set = set()
    for kind in kinds:
        suppressed |= _TRIGGER_TRUMPS.get(kind, frozenset())
    survivors = [t for t in triggers if t.kind not in suppressed]
    return random.choice(survivors or list(triggers))


@dataclass
class Trigger:
    """One matched trigger plus the data its templates need to render.

    ``kind`` keys into :data:`_TEMPLATES_BY_KIND`. ``format_data`` is
    a dict of fields the templates can interpolate (``{name}``,
    ``{game}``, ``{new}``, ``{prior}``, ``{prior_holder}``…). Detector
    functions only need to populate the fields their pool actually
    uses — extra fields are ignored, missing ones raise at
    render-time which is loud and easy to fix.
    """

    kind: str
    format_data: Dict[str, str] = field(default_factory=dict)


def _detect_personal_history_trigger(
    prior_raws: List[int], new_raw: int
) -> Optional[Trigger]:
    """Existing PB / tied-PB / worst / tied-worst detection. Returns
    a :class:`Trigger` whose ``format_data`` already has ``new`` and
    (where applicable) ``prior`` populated as raw integers — caller
    formats them to display strings."""
    if not prior_raws:
        return None
    best_before = min(prior_raws)
    worst_before = max(prior_raws)
    if new_raw < best_before:
        return Trigger(
            kind="new_pb",
            format_data={"_new_raw": str(new_raw), "_prior_raw": str(best_before)},
        )
    if new_raw == best_before:
        return Trigger(
            kind="tied_pb",
            format_data={"_new_raw": str(new_raw)},
        )
    if new_raw > worst_before:
        return Trigger(
            kind="new_worst",
            format_data={"_new_raw": str(new_raw), "_prior_raw": str(worst_before)},
        )
    if new_raw == worst_before:
        return Trigger(
            kind="tied_worst",
            format_data={"_new_raw": str(new_raw)},
        )
    return None


def _detect_today_extreme_trigger(
    today_scores: Sequence[ScoreRow], new_raw: int, player_id: int
) -> Optional[Trigger]:
    """If the just-inserted score is currently the best (or worst) of
    the day across all players, return the appropriate trigger.

    Requires at least two distinct players for the day so a "first
    submission of the day, trivially best" doesn't fire — the trigger
    only feels earned when there's a field to beat.
    """
    distinct_players = {s.player_id for s in today_scores}
    if len(distinct_players) < 2:
        return None

    raws = [s.raw_score for s in today_scores]
    fastest = min(raws)
    slowest = max(raws)

    # The just-inserted row is in today_scores; ``new_raw`` is the
    # caller's score. We treat ties for fastest/slowest cleanly: only
    # claim the trigger when this player's score is strictly the
    # extreme, OR tied at the extreme with no one currently above
    # them. The simplest safe check: this player's row matches the
    # extreme and nobody else with a strictly more-extreme score
    # exists in the field.
    others_below = [s.raw_score for s in today_scores if s.player_id != player_id]
    if not others_below:
        return None

    if new_raw < min(others_below):
        return Trigger(
            kind="best_of_day",
            format_data={"_new_raw": str(new_raw)},
        )
    if new_raw > max(others_below):
        return Trigger(
            kind="worst_of_day",
            format_data={"_new_raw": str(new_raw)},
        )
    return None


def _detect_all_time_record_trigger(
    fastest_top: List[ScoreRow],
    slowest_top: List[ScoreRow],
    new_raw: int,
    player_id: int,
) -> Optional[Trigger]:
    """Return a record / anti-record trigger when the just-inserted
    row is the all-time fastest or slowest for the game.

    ``fastest_top`` and ``slowest_top`` come from
    :meth:`Repository.get_top_extremes_for_game` (top-N each side,
    most-extreme first). The just-inserted row is already in the DB
    when this runs, so ``fastest_top[0]`` either IS this player's
    submission (record!) or someone else's (no record).

    The "previous holder" for the message is the runner-up — i.e.
    ``fastest_top[1]`` when [0] is the just-inserted row. We only
    fire the trigger when there's a real previous holder to name,
    avoiding the awkward "first-ever submission ⇒ trivially the
    record" case.
    """
    record_trigger = _make_extreme_trigger(
        kind="all_time_record",
        top=fastest_top,
        new_raw=new_raw,
        player_id=player_id,
    )
    if record_trigger is not None:
        return record_trigger
    return _make_extreme_trigger(
        kind="all_time_anti_record",
        top=slowest_top,
        new_raw=new_raw,
        player_id=player_id,
    )


def _make_extreme_trigger(
    *,
    kind: str,
    top: List[ScoreRow],
    new_raw: int,
    player_id: int,
) -> Optional[Trigger]:
    """Helper: turn a top-N list (fastest or slowest) into a Trigger
    when the leader IS the just-inserted row and there's a runner-up
    to credit as the prior holder."""
    if len(top) < 2:
        return None  # need a runner-up to name as the previous holder
    leader, runner_up = top[0], top[1]
    if leader.raw_score != new_raw or leader.player_id != player_id:
        return None
    if runner_up.player_id == player_id:
        # The just-inserted player held BOTH first AND second place
        # already. Awkward to phrase; skip rather than mislead.
        return None
    return Trigger(
        kind=kind,
        format_data={
            "_new_raw": str(new_raw),
            "_prior_raw": str(runner_up.raw_score),
            "prior_holder": runner_up.player_name or "—",
        },
    )


def gather_submission_triggers(
    repo: Repository,
    *,
    player_id: int,
    game: str,
    new_raw: int,
    today: date,
    prior_raws: List[int],
) -> List[Trigger]:
    """Run every detector against the just-inserted submission and
    return the list of triggers that fired. Caller picks one (at
    random or by some other strategy).

    Detectors are independent — a single submission can fire
    multiple triggers (e.g. a personal best AND best of today).
    Order in the returned list is stable but irrelevant; callers
    that want randomness should use :func:`random.choice`.
    """
    triggers: List[Trigger] = []

    personal = _detect_personal_history_trigger(prior_raws, new_raw)
    if personal is not None:
        triggers.append(personal)

    try:
        today_scores = repo.list_today_for_game(game=game, day=today)
    except Exception:
        logger.exception(
            "list_today_for_game failed (game=%s, day=%s) — skipping today triggers",
            game,
            today,
        )
        today_scores = []
    today_extreme = _detect_today_extreme_trigger(today_scores, new_raw, player_id)
    if today_extreme is not None:
        triggers.append(today_extreme)

    try:
        fastest_top, slowest_top = repo.get_top_extremes_for_game(game=game, n=2)
    except Exception:
        logger.exception(
            "get_top_extremes_for_game failed (game=%s) — skipping all-time triggers",
            game,
        )
        fastest_top, slowest_top = [], []
    record = _detect_all_time_record_trigger(
        fastest_top, slowest_top, new_raw, player_id
    )
    if record is not None:
        triggers.append(record)

    # Phase D: finer slices of personal history. We pull the player's
    # full game history once and hand the same list to every Phase-D
    # detector — keeps the repo round-trips down to one even when
    # several Phase-D triggers could match.
    try:
        player_game_history = [
            s for s in repo.list_player_scores(player_id) if s.game == game
        ]
    except Exception:
        logger.exception(
            "list_player_scores failed (player=%s) — skipping Phase D triggers",
            player_id,
        )
        player_game_history = []

    dow = _detect_dow_extreme_trigger(player_game_history, new_raw, today)
    if dow is not None:
        triggers.append(dow)

    year = _detect_year_extreme_trigger(player_game_history, new_raw, today)
    if year is not None:
        triggers.append(year)

    first = _detect_first_today_trigger(today_scores, new_raw, player_id)
    if first is not None:
        triggers.append(first)

    return triggers


# ---------------------------------------------------------------------------
# Phase D detectors
# ---------------------------------------------------------------------------


_WEEKDAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
)


def _detect_dow_extreme_trigger(
    player_game_history: Sequence[ScoreRow],
    new_raw: int,
    today: date,
) -> Optional[Trigger]:
    """Fire when the just-inserted score is the player's best (or
    worst) ever on this game on this weekday. Requires at least one
    prior submission on the same weekday — first-ever-Tuesday-Queens
    isn't an interesting "Tuesday PB" callout.

    The just-inserted row is in ``player_game_history``. We compare
    against the OTHER same-weekday rows (i.e. exclude the one we
    just wrote)."""
    weekday = today.weekday()
    same_dow = [
        s for s in player_game_history
        if s.puzzle_date.weekday() == weekday and s.puzzle_date != today
    ]
    if not same_dow:
        return None

    raws = [s.raw_score for s in same_dow]
    weekday_name = _WEEKDAY_NAMES[weekday]

    if new_raw < min(raws):
        return Trigger(
            kind="dow_pb",
            format_data={
                "_new_raw": str(new_raw),
                "_prior_raw": str(min(raws)),
                "weekday": weekday_name,
            },
        )
    if new_raw > max(raws):
        return Trigger(
            kind="dow_worst",
            format_data={
                "_new_raw": str(new_raw),
                "_prior_raw": str(max(raws)),
                "weekday": weekday_name,
            },
        )
    return None


def _detect_year_extreme_trigger(
    player_game_history: Sequence[ScoreRow],
    new_raw: int,
    today: date,
) -> Optional[Trigger]:
    """Fire when the just-inserted score is the player's best of the
    calendar year for this game. Requires at least one prior in the
    same year — January 2nd's first-ever-of-the-year submission
    isn't an interesting "year PB".

    Only positive (PB) variant for now — the year-worst case overlaps
    too much with the personal-worst trigger to feel distinct.
    """
    year = today.year
    same_year = [
        s for s in player_game_history
        if s.puzzle_date.year == year and s.puzzle_date != today
    ]
    if not same_year:
        return None

    raws = [s.raw_score for s in same_year]
    if new_raw < min(raws):
        return Trigger(
            kind="year_pb",
            format_data={
                "_new_raw": str(new_raw),
                "_prior_raw": str(min(raws)),
                "year": str(year),
            },
        )
    return None


def _detect_first_today_trigger(
    today_scores: Sequence[ScoreRow],
    new_raw: int,
    player_id: int,
) -> Optional[Trigger]:
    """Fire when the just-inserted row is the only score for this
    game today — i.e. no other player has put one down yet, and
    this player hasn't double-submitted (which the unique constraint
    prevents anyway).

    Mutually exclusive with ``best_of_day`` / ``worst_of_day`` (which
    require ≥ 2 distinct players on the day) so a "you broke the
    seal" callout doesn't compete with a "best of day" callout for
    a solo first-mover."""
    if len(today_scores) != 1:
        return None
    only = today_scores[0]
    if only.player_id != player_id or only.raw_score != new_raw:
        return None
    return Trigger(
        kind="first_today",
        format_data={"_new_raw": str(new_raw)},
    )


def render_trigger(trigger: Trigger, *, player_name: str, game: str) -> str:
    """Format ``trigger`` into the DM body. Pulls a random template
    from the trigger's pool and resolves placeholders from the format
    data and the caller-supplied identity bits.

    Supported placeholders (templates use a subset based on kind):
    ``{name}``, ``{game}``, ``{new}``, ``{prior}``, ``{prior_holder}``,
    ``{weekday}``, ``{year}``.
    """
    pool = _TEMPLATES_BY_KIND[trigger.kind]
    template = random.choice(pool)
    game_label = GAME_DISPLAY.get(game, game)

    fd = trigger.format_data
    subs: Dict[str, Any] = {
        "name": player_name,
        "game": game_label,
    }
    if "_new_raw" in fd:
        subs["new"] = format_raw_score(game, int(fd["_new_raw"]))
    if "_prior_raw" in fd:
        subs["prior"] = format_raw_score(game, int(fd["_prior_raw"]))
    for key in ("prior_holder", "weekday", "year"):
        if key in fd:
            subs[key] = fd[key]
    return template.format(**subs)


def render_personal_best_message(
    *,
    player_name: str,
    game: str,
    new_raw: int,
    prior_raws: list[int],
) -> Optional[str]:
    """Pure-function core preserved for the personal-history-only
    path. Used by tests that don't want to stub a repo. The full
    multi-trigger entry point is :func:`maybe_notify_personal_best`."""
    trigger = _detect_personal_history_trigger(prior_raws, new_raw)
    if trigger is None:
        return None
    return render_trigger(trigger, player_name=player_name, game=game)


def maybe_notify_personal_best(
    repo: Repository,
    settings: Optional[Settings],
    *,
    player: Player,
    game: str,
    new_raw: int,
    today: Optional[date] = None,
    deliver: bool = True,
) -> Optional[str]:
    """DM ``player`` a one-line zinger when the just-inserted submission
    fires any of the trigger detectors (PB, tied PB, personal worst,
    tied worst, best of today, worst of today, all-time record,
    all-time anti-record).

    When more than one trigger fires, picks one at random — with the
    single exception that a lifetime PB / worst trumps its weekday
    and year siblings (see :func:`_pick_trigger`). Returns the DM
    body sent, or ``None`` when no triggers fired (or ``settings`` is
    ``None``, the in-webhook fallback when Twilio isn't configured —
    tests exercise this path).

    Exceptions are caught and logged so a notification failure can't
    sink the webhook reply to the original submission.

    ``deliver`` toggles whether a separate Twilio DM goes out. The
    webhook calls this with ``deliver=False`` so the trigger body
    can be folded into the score-confirmation reply instead of
    arriving as a second message; the body is returned directly for
    the caller to append.
    """
    try:
        all_scores = repo.list_player_scores(player.id)
    except Exception:
        logger.exception(
            "Failed to load player history for trigger check (player=%s)", player.id
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
            "skipping trigger check",
            game,
            new_raw,
            player.id,
        )
        return None

    # Resolve today's puzzle date for the today-extreme trigger.
    # Caller can pass it explicitly (the webhook already has it as
    # ``puzzle_date``); fall back to ``date.today()`` for ad-hoc
    # invocations and tests.
    today = today or date.today()

    triggers = gather_submission_triggers(
        repo,
        player_id=player.id,
        game=game,
        new_raw=new_raw,
        today=today,
        prior_raws=game_raws,
    )
    if not triggers:
        return None

    chosen = _pick_trigger(triggers)
    body = render_trigger(chosen, player_name=player.display_name, game=game)

    if not deliver:
        # Inline mode: caller folds the message into a larger reply,
        # so we just hand back the rendered body without touching
        # Twilio.
        return body

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

    # Weekly standing. Mirror the daily-recap / weekly-wrap filter
    # (scheduler.daily_recap, scheduler.weekly_wrap) so disabled-game
    # scores don't inflate the running total — otherwise this line
    # disagrees with the recap / wrap totals the player will see next.
    week_filtered = [s for s in week_scores if s.game in enabled_games]
    lb = weekly_leaderboard(week_filtered)
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
    deliver: bool = True,
) -> Optional[str]:
    """DM ``player`` a summary when this submission means they've now
    played every enabled game for ``today``. Returns the DM body
    sent, or ``None`` when the player isn't done yet.

    Idempotent-in-practice: "has every game" only becomes true once
    per day per player (further submissions for an already-done game
    bounce off the uniqueness constraint). Callers still wrap the
    invocation in try/except so a transient DB hiccup can't mask the
    webhook ack.

    ``deliver`` toggles whether a separate Twilio DM goes out. The
    webhook calls this with ``deliver=False`` so the summary can
    ride inside the score-confirmation reply rather than arrive as
    a second message.
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

    if not deliver:
        return body

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
