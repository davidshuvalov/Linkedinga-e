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
from datetime import date, timedelta
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from .config import Settings
from .db import Player, Repository, ScoreRow
from .parsers import GAME_DISPLAY, GAME_DISPLAY_ORDER, format_raw_score
from .puzzles import week_bounds
from .scoring import assign_daily_points, weekly_leaderboard
from .sender import send_dm, send_recap

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
    "{name} just put down the best {game} of their life: {new}. {prior} is now history.",
    "That's a personal record, {name}: {game} in {new} (was {prior}). Not a drill.",
    "Unprecedented {game} from {name}: {new}. The previous {prior} didn't see this coming.",
    "{name}: {new} on {game}. Your best ever (was {prior}). The data is thrilled.",
    "A personal best to savour, {name}: {game} in {new} (was {prior}). This one counts.",
    "{name}, the {game} gods smiled: {new} (was {prior}). Down. Done. Documented.",
    "PB, {name}. {game} in {new}. The {prior} is in your rear-view mirror and shrinking.",
    "{name} dropped a {game} PB nobody asked for but everyone respects: {new} (was {prior}).",
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
    "Spiritual damage: {name} clocks {new} on {game}. Previous worst: {prior}. The floor has a sub-basement.",
    "{name}, {new} on {game} is your new worst. The old {prior} now looks like a golden era.",
    "The bad news: {name}'s {game} in {new} is a new worst. The worse news: {prior} was your previous floor.",
    "Historically bad, {name}: {new} on {game} is your worst ever (was {prior}). But you're still a good person. Unlike this score.",
    "{name} just redefined their {game} floor: {new}. The old {prior} now feels like a triumph.",
    "{name}, {game} in {new}. New personal worst. The {prior} didn't deserve to be beaten like this.",
    "The puzzle won comprehensively, {name}: {new} on {game} (was {prior}). Tomorrow is another day.",
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
    "{name}: {new} on {game}. Day's fastest. The lead may not last but it is yours right now.",
    "Top of today's {game} pile: {name} with {new}. Justified smugness follows.",
    "{name} takes today's {game} crown: {new}. Everyone else is now playing catch-up.",
    "{name}, today's {game} benchmark is set at {new}. Most won't beat it. Enjoy.",
    "First in quality on {game} today: {name}, {new}. The others have a number to chase.",
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
    "{name}, {new} on {game} is the day's worst so far. The puzzle has claimed a victim.",
    "Rock bottom of today's {game} leaderboard: {name}, {new}. The view from down here is unique.",
    "{name}: today's {game} sacrifice. Someone had to hold up the bottom. Today it's you.",
    "{name}, you're today's {game} anchor: {new}. Keeping the group's average honest.",
    "Dead last on {game} today, {name}: {new}. That's not nothing. It's just last.",
    "{name}, the {game} board has your name at the bottom: {new}. Proudly yours.",
    "Last man standing on {game} today — standing at the bottom: {name}, {new}.",
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


# Group DOW record — fastest score for this game on this weekday
# (e.g. Monday Queens) across all players in the group, all time.
_GROUP_DOW_RECORD_TEMPLATES = (
    "NEW group {weekday} {game} record! {name} clocked {new}. {prior_holder}'s {prior} is history.",
    "{name} just set the best-ever {weekday} {game} for this group: {new} (was {prior_holder}, {prior}).",
    "Group {weekday} record on {game} falls to {name}: {new}. {prior_holder} held it with {prior}.",
    "{name} rewrote the group's {weekday} {game} record: {new} beats {prior_holder}'s {prior}.",
    "Best {weekday} {game} this group has ever seen: {name}, {new}. {prior_holder}'s {prior} is dethroned.",
    "The {weekday} {game} crown changes hands — {name} with {new} edges out {prior_holder}'s {prior}.",
    "{name} set a new group benchmark for {game} on {weekday}s: {new} (was {prior_holder}, {prior}).",
    "Group {weekday} {game} record shattered: {name} clocks {new}. {prior_holder}'s {prior} is a footnote now.",
    "{name} owns {weekday} {game} in this group now: {new}, beating {prior_holder}'s {prior}.",
    "New {weekday} {game} high-water mark for the group — {name} with {new}. {prior_holder}'s old {prior} is retired.",
    "History made on a {weekday}: {name} posted {new} on {game}, best this group has ever seen on this day (was {prior_holder}, {prior}).",
    "{name} just carved their name into {weekday} {game} history: {new}. {prior_holder}'s {prior} finally falls.",
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


# 2nd–5th best in personal all-time history for this game.
# ``{rank}`` is filled with "2nd", "3rd", etc.
_NTH_BEST_TEMPLATES = (
    "Not the PB, but your {rank} best {game} ever: {new}. The top spot stays just out of reach.",
    "{name}, that's your {rank} best {game} of all time: {new}. Podium-adjacent.",
    "{rank} best {game} you've ever done, {name}: {new}. The PB is right there, judging you.",
    "Silver-ish medal, {name}: {new} on {game} is your {rank} ever. Getting there.",
    "{name}: {new} on {game}. Your {rank} best. Not bad, considering.",
    "Historically, this {game} ranks {rank} for you: {new}. A good one, technically.",
    "{rank} all-time on {game} for {name}: {new}. The best version of you is only marginally faster.",
    "Not a PB, but {rank} best on {game}: {new}, {name}. Consistency is charming.",
    "{name}'s {rank} best {game}: {new}. Still chasing that number one spot.",
    "Career {rank} on {game}, {name}: {new}. Better than most days. Just not the best.",
    "{rank} on your {game} all-time list: {new}. {name}, you are, technically, improving.",
    "{name}: {new} on {game}. {rank} best ever. That's a flex, with conditions.",
    "That {game} in {new} is your {rank} all-time, {name}. Conditional respect.",
    "Good but not great: {new} on {game} ranks {rank} for you, {name}. The PB is unmoved.",
    "{name} scores {new} on {game} — {rank} best in personal history. Acceptable. Just.",
    "{name}, {new} on {game} makes the all-time {rank}. You are, statistically, decent.",
    "{rank} best ever on {game}, {name}: {new}. The numbers note your existence.",
    "Not a disaster, not a triumph: {new} on {game} is your {rank} best, {name}.",
)

# Top 25% of player's own all-time history — not a PB.
_TOP_QUARTILE_TEMPLATES = (
    "Top 25% of your {game} history, {name}: {new}. The good version of you showed up.",
    "{name}, {new} on {game} is in your upper quartile historically. Take it.",
    "That's one of your better {game} days: {new}. Top 25% for you.",
    "Top quartile for {name} on {game}: {new}. Quietly excellent.",
    "{new} puts you in the top quarter of your own {game} history, {name}. Solid.",
    "{name}: {new} on {game}. Upper 25% for you. Comfortably decent.",
    "Your above-average {game} self turned up today, {name}: {new}.",
    "Historically a good {game} score for you, {name}: {new}. Top quartile.",
    "{name}, that {new} on {game} sits comfortably in your top 25% all-time.",
    "One of your better {game} performances: {new}, {name}. Top quartile.",
    "Upper echelon of your own {game} history: {new}, {name}. You can do this.",
    "{name} delivers a top-quartile {game}: {new}. The data agrees.",
)

# Bottom 25% of player's own all-time history — not a worst.
_BOTTOM_QUARTILE_TEMPLATES = (
    "Bottom quarter of your {game} history, {name}: {new}. Not your finest.",
    "{name}, {new} on {game} is in the bottom 25% of your all-time scores. Historically bleak.",
    "That's a low-end {game} for you: {new}. Bottom quartile, historically speaking.",
    "One of your slower {game} days: {new}, {name}. The bottom 25% of your own record.",
    "{name}: {new} on {game}. Lower quartile for you. But you showed up, and that's something. Sort of.",
    "Historically, {new} is near the bottom for you on {game}, {name}. But you're a good person, unlike this score.",
    "Bottom 25% on {game}, {name}: {new}. The puzzle had its way with you today.",
    "{name}, {new} is one of your weaker {game} scores. Lower quartile. The bar was set. It was cleared. From underneath.",
    "Lower quarter of your {game} history: {new}, {name}. The better days are in the data.",
    "{name}: {new} on {game} lands in your bottom quartile. Statistically, 25% of your games are like this. It's still not ideal.",
    "Not one of your better {game} days, {name}: {new}. Bottom 25% personally. We say this with love.",
    "{name}, that {game} in {new} is bottom quartile for you. But at least you're a good person. Unlike the score.",
    "Lower end for you on {game}: {new}. {name}, the data is not rooting for you today.",
)

# Top 25% of player's same-weekday history.
_DOW_TOP_QUARTILE_TEMPLATES = (
    "{name}, your {weekday} {game} is looking sharp: {new} is top quartile for you on {weekday}s.",
    "Top 25% of your {weekday} {game} scores: {new}, {name}. {weekday}s suit you.",
    "{weekday} is a good {game} day for you, {name}: {new} in the top quarter historically.",
    "{name}, that {new} on {game} makes it one of your better {weekday}s. Top quartile.",
    "Strong {weekday} {game}, {name}: {new} is upper quartile for you on {weekday}s.",
    "{name}: a good {weekday} for {game}. {new} is top 25% of your {weekday} history.",
)

# Bottom 25% of player's same-weekday history.
_DOW_BOTTOM_QUARTILE_TEMPLATES = (
    "{weekday}s and {game} just don't mix for you, {name}: {new} is bottom quartile on {weekday}s.",
    "{name}, historically {weekday}s are rough for you on {game}: {new} is in the lower quarter.",
    "Bottom 25% of your {weekday} {game} scores: {new}, {name}. The pattern holds.",
    "{name}: {new} on {game} on a {weekday}. Historically, {weekday}s are not your {game} day.",
    "{weekday}-{game} lows, {name}: {new} is bottom quartile for you on {weekday}s. Maybe it's the vibes.",
    "{name}, the data suggests {weekday} is not your {game} day. {new} confirms it.",
)

# Player had a bad score but wasn't the worst of the day — there's
# someone worse to point at. ``{prior_holder}`` is the actual worst;
# ``{prior}`` is their score.
_ABOVE_FLOOR_TODAY_TEMPLATES = (
    "Not your best {game}: {new}, {name}. But spare a thought for {prior_holder} who managed {prior}.",
    "{name}, {new} on {game} isn't great. Then again, neither is {prior_holder}'s {prior}. Small comfort.",
    "Rough {game} today, {name}: {new}. At least {prior_holder} is having a worse one ({prior}).",
    "{name}: {new} on {game}. Would feel bad, but {prior_holder} posted {prior}. Context matters.",
    "That {game} in {new} was rough, {name}. {prior_holder}'s {prior} makes yours look quick.",
    "{name}, the puzzle beat you ({new}). It beat {prior_holder} harder though ({prior}). There's comfort in the rankings.",
    "Bad {game} for {name}: {new}. {prior_holder}'s {prior} is worse. Barely. Take it.",
    "{name}, you're not winning {game} today ({new}). But {prior_holder} has it worse at {prior}.",
    "{new} on {game} — not ideal, {name}. Still faster than {prior_holder}'s {prior}. Today's gift.",
    "{name}: {new} on {game} isn't what you wanted. But {prior_holder} posted {prior}. Perspective.",
    "You did bad on {game} today ({new}), {name}. {prior_holder} did worse ({prior}). Character-building.",
    "{name}, {new} is a slow {game}. {prior_holder}'s {prior} is slower. You are above the floor. The floor is carpeted.",
    "Not your finest {game}, {name}: {new}. {prior_holder}'s {prior} gives you the moral high ground.",
    "{name}: {new} on {game}. Underwhelming. But {prior_holder} ({prior}) somehow makes {new} look quick.",
    "The puzzle won, {name}. {new} on {game} is slow. But {prior_holder} gave it {prior}. You're not alone at the bottom. Just adjacent.",
    "{name}, {new} wasn't pretty on {game}. At least {prior_holder} is down there too ({prior}). Solidarity. Sort of.",
)


_RIVALRY_TEMPLATES = (
    "You and {rival} have been within {gap} pts for {weeks} weeks. That's not a gap — that's a rivalry.",
    "{rival} is your shadow on the leaderboard. {weeks} weeks running, neck and neck, {name}.",
    "Fun fact: you and {rival} have finished within {gap} pts of each other for {weeks} weeks straight.",
    "{weeks} weeks. You. {rival}. {gap} pts. The leaderboard is basically a two-horse race at this point.",
    "{name}, {rival} is following you so closely on the leaderboard they could be your echo. {weeks} weeks and counting.",
    "Statistically, you and {rival} are the same person. {weeks} weeks within {gap} pts. Rivalry confirmed.",
    "Just so you know: {rival} has finished within {gap} pts of you for {weeks} weeks in a row. Might want to do something about that.",
    "{name} and {rival}: {weeks} weeks, {gap} pts average gap. The algorithm is enjoying this more than you are.",
    "You've been {gap} pts from {rival} for {weeks} weeks. Either you're evenly matched or one of you is very annoying.",
    "The data wants you to know that {rival} is within {gap} pts. {weeks} weeks of this. Act accordingly, {name}.",
    "{rival} is {gap} pts behind you on average. For {weeks} weeks. That's not background noise — that's a rival.",
    "Friendly reminder: {rival} has been in your pocket for {weeks} weeks. {gap} pts is uncomfortably close.",
    "{name}: {weeks} weeks, {rival} nipping at your heels within {gap} pts. This is what competition looks like.",
    "Your week-to-week nemesis has been confirmed: {rival}. {weeks} consecutive weeks within {gap} pts.",
    "The leaderboard doesn't lie: {name} and {rival} have been inseparable for {weeks} weeks. {gap} pts apart.",
    "This is getting personal. {rival} has tracked you within {gap} pts for {weeks} straight weeks, {name}.",
    "{rival} keeps showing up right behind you. {weeks} weeks, {gap} pts. Suspicious, frankly.",
    "It appears {rival} has adopted you as their personal benchmark. {weeks} weeks within {gap} pts. You're welcome?",
    "The closest thing to a rivalry you didn't know you had: {name} vs {rival}. {weeks} weeks within {gap} pts.",
    "Filed under: unwanted competition. {rival} within {gap} pts for {weeks} weeks. They're not going anywhere.",
)


_PODIUM_STREAK_TEMPLATES = (
    "Podium machine, {name}! That's {weeks} weeks in the top 3.",
    "{name} is making the podium look easy — {weeks} straight weeks in the top 3.",
    "{weeks} weeks on the podium and counting, {name}. Consistent.",
    "Don't look now but {name} has been in the top 3 for {weeks} weeks running.",
    "{name}: {weeks} consecutive weeks in the podium zone. The leaderboard is starting to feel like home.",
    "Top 3 for {weeks} weeks? That's not luck, {name}. That's dominance.",
    "{name} is on a {weeks}-week podium streak. Someone's built for this.",
    "Podium streak alert — {name}, {weeks} weeks and no sign of stopping.",
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
    "group_dow_record": _GROUP_DOW_RECORD_TEMPLATES,
    "dow_pb": _DOW_PB_TEMPLATES,
    "dow_worst": _DOW_WORST_TEMPLATES,
    "year_pb": _YEAR_PB_TEMPLATES,
    "first_today": _FIRST_TODAY_TEMPLATES,
    "nth_best_personal": _NTH_BEST_TEMPLATES,
    "top_quartile_personal": _TOP_QUARTILE_TEMPLATES,
    "bottom_quartile_personal": _BOTTOM_QUARTILE_TEMPLATES,
    "dow_top_quartile_personal": _DOW_TOP_QUARTILE_TEMPLATES,
    "dow_bottom_quartile_personal": _DOW_BOTTOM_QUARTILE_TEMPLATES,
    "above_floor_today": _ABOVE_FLOOR_TODAY_TEMPLATES,
    "rivalry": _RIVALRY_TEMPLATES,
    "podium_streak": _PODIUM_STREAK_TEMPLATES,
}


# Trigger pick-time precedence: when a lifetime PB (or PB-tie) fires,
# the weekday-PB and year-PB triggers are subsumed by it — saying
# "fastest Friday Queens" alongside "fastest Queens ever" buries the
# lede. Same for the worst side. The trump map filters out the
# subordinate kinds whenever the dominant kind is in the candidate
# pool; the random pick then runs over the survivors.
_TRIGGER_TRUMPS: Dict[str, frozenset] = {
    "new_pb": frozenset({
        "dow_pb", "year_pb",
        "nth_best_personal", "top_quartile_personal", "dow_top_quartile_personal",
        "rivalry", "podium_streak",
    }),
    "tied_pb": frozenset({
        "dow_pb", "year_pb",
        "nth_best_personal", "top_quartile_personal", "dow_top_quartile_personal",
        "rivalry", "podium_streak",
    }),
    "new_worst": frozenset({
        "dow_worst",
        "bottom_quartile_personal", "dow_bottom_quartile_personal",
    }),
    "tied_worst": frozenset({
        "dow_worst",
        "bottom_quartile_personal", "dow_bottom_quartile_personal",
    }),
    "all_time_record": frozenset({
        "new_pb", "tied_pb", "best_of_day",
        "dow_pb", "year_pb", "group_dow_record",
        "nth_best_personal", "top_quartile_personal", "dow_top_quartile_personal",
        "rivalry", "podium_streak",
    }),
    "all_time_anti_record": frozenset({
        "new_worst", "tied_worst", "worst_of_day",
        "dow_worst",
        "bottom_quartile_personal", "dow_bottom_quartile_personal", "above_floor_today",
    }),
    "group_dow_record": frozenset({
        "new_pb", "tied_pb", "best_of_day",
        "dow_pb", "year_pb",
        "nth_best_personal", "top_quartile_personal", "dow_top_quartile_personal",
        "rivalry", "podium_streak",
    }),
    "best_of_day": frozenset({
        "nth_best_personal", "top_quartile_personal", "dow_top_quartile_personal",
        "above_floor_today",
        "rivalry",
    }),
    "worst_of_day": frozenset({
        "bottom_quartile_personal", "dow_bottom_quartile_personal",
        "above_floor_today",
    }),
    "rivalry": frozenset(),
    "podium_streak": frozenset(),
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


def _detect_group_dow_record_trigger(
    fastest_dow_top: List[ScoreRow],
    new_raw: int,
    player_id: int,
    weekday: int,
) -> Optional[Trigger]:
    """Return a ``group_dow_record`` trigger when the just-inserted score
    is the all-time fastest for this game on this weekday within the group.

    Requires a runner-up with a strictly worse score so we only fire on a
    genuine improvement (not a tie), and can name the previous record holder.
    """
    if len(fastest_dow_top) < 2:
        return None
    leader, runner_up = fastest_dow_top[0], fastest_dow_top[1]
    if leader.raw_score != new_raw or leader.player_id != player_id:
        return None
    if runner_up.raw_score == new_raw:
        return None  # tied the record, not a strict improvement
    if runner_up.player_id == player_id:
        return None  # player holds both spots
    return Trigger(
        kind="group_dow_record",
        format_data={
            "_new_raw": str(new_raw),
            "_prior_raw": str(runner_up.raw_score),
            "prior_holder": runner_up.player_name or "—",
            "weekday": _WEEKDAY_NAMES[weekday],
        },
    )


def _detect_nth_best_trigger(prior_raws: List[int], new_raw: int) -> Optional[Trigger]:
    """Fire when ``new_raw`` ranks 2nd–5th in the player's personal history.

    Requires ≥ 6 prior scores so "2nd best of 3" (which isn't interesting)
    doesn't fire. PB and tied-PB are handled by the personal-history detector.
    """
    if len(prior_raws) < 6:
        return None
    if new_raw <= min(prior_raws):
        return None  # PB / tied-PB — handled elsewhere
    better_count = sum(1 for r in prior_raws if r < new_raw)
    rank = better_count + 1
    if rank < 2 or rank > 5:
        return None
    ordinals = {2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}
    return Trigger(
        kind="nth_best_personal",
        format_data={"_new_raw": str(new_raw), "rank": ordinals[rank]},
    )


def _detect_quartile_trigger(prior_raws: List[int], new_raw: int) -> Optional[Trigger]:
    """Fire when ``new_raw`` lands in the top or bottom 25% of the
    player's personal history, but isn't itself a PB or worst.

    Requires ≥ 8 prior scores so a quartile is statistically meaningful.
    """
    if len(prior_raws) < 8:
        return None
    if new_raw <= min(prior_raws) or new_raw >= max(prior_raws):
        return None  # PB / worst handled elsewhere
    n = len(prior_raws)
    worse_count = sum(1 for r in prior_raws if r > new_raw)
    better_count = sum(1 for r in prior_raws if r < new_raw)
    if worse_count / n >= 0.75:
        return Trigger(kind="top_quartile_personal", format_data={"_new_raw": str(new_raw)})
    if better_count / n >= 0.75:
        return Trigger(kind="bottom_quartile_personal", format_data={"_new_raw": str(new_raw)})
    return None


def _detect_dow_quartile_trigger(
    player_game_history: Sequence[ScoreRow],
    new_raw: int,
    today: date,
) -> Optional[Trigger]:
    """Fire when ``new_raw`` is in the top or bottom 25% of the player's
    scores for this game on this weekday. Requires ≥ 4 prior same-weekday
    scores; PB/worst on the weekday are handled by ``dow_pb``/``dow_worst``."""
    weekday = today.weekday()
    same_dow = [
        s.raw_score for s in player_game_history
        if s.puzzle_date.weekday() == weekday and s.puzzle_date != today
    ]
    if len(same_dow) < 4:
        return None
    if new_raw <= min(same_dow) or new_raw >= max(same_dow):
        return None
    n = len(same_dow)
    worse_count = sum(1 for r in same_dow if r > new_raw)
    better_count = sum(1 for r in same_dow if r < new_raw)
    weekday_name = _WEEKDAY_NAMES[weekday]
    if worse_count / n >= 0.75:
        return Trigger(
            kind="dow_top_quartile_personal",
            format_data={"_new_raw": str(new_raw), "weekday": weekday_name},
        )
    if better_count / n >= 0.75:
        return Trigger(
            kind="dow_bottom_quartile_personal",
            format_data={"_new_raw": str(new_raw), "weekday": weekday_name},
        )
    return None


def _detect_above_floor_today_trigger(
    today_scores: Sequence[ScoreRow],
    new_raw: int,
    player_id: int,
) -> Optional[Trigger]:
    """Fire when the player had a bad score but wasn't the worst of the day.

    Only fires when the player is in the slower half of today's field,
    so it doesn't console someone who was actually competitive. Names
    the actual day-worst player as the consolation reference."""
    others = [s for s in today_scores if s.player_id != player_id]
    if not others:
        return None
    worst_other = max(others, key=lambda s: s.raw_score)
    if worst_other.raw_score <= new_raw:
        return None  # Player IS the worst or tied for it
    # Only trigger for the slower half of today's field.
    all_today = sorted(s.raw_score for s in today_scores)
    median = all_today[len(all_today) // 2]
    if new_raw <= median:
        return None
    return Trigger(
        kind="above_floor_today",
        format_data={
            "_new_raw": str(new_raw),
            "_prior_raw": str(worst_other.raw_score),
            "prior_holder": worst_other.player_name or "—",
        },
    )



def _detect_podium_streak_trigger(
    repo: Repository,
    player_id: int,
    player_name: str,
    group_id: int,
    today: date,
    enabled_games: FrozenSet[str],
) -> Optional[Trigger]:
    """Fire when a player extends their podium streak to 3 or more weeks.

    Cooldown: once per player per week via recap_log.
    """
    from .puzzles import week_bounds as _wb
    from .stats import podium_streak

    monday, _ = _wb(today)
    cooldown_key = f"podium_streak:{player_id}:{monday.isoformat()}"
    try:
        if repo.has_recap_been_sent(today, cooldown_key, group_id=group_id):
            return None
    except Exception:
        logger.exception("podium_streak: cooldown check failed")
        return None

    streak = podium_streak(
        repo,
        player_id=player_id,
        group_id=group_id,
        reference_week_monday=monday,
        enabled_games=enabled_games,
    )
    if streak < 3:
        return None

    try:
        repo.mark_recap_sent(today, cooldown_key, group_id=group_id)
    except Exception:
        logger.exception("podium_streak: mark_recap_sent failed")
        return None

    return Trigger(
        kind="podium_streak",
        format_data={"weeks": str(streak)},
    )


_RIVALRY_THRESHOLD = 3.0  # pts; within this margin = "close"


def _detect_rivalry_trigger(
    repo: "Repository",
    player_id: int,
    group_id: int,
    today: date,
    settings: "Settings",
) -> Optional["Trigger"]:
    """Fire when a player has finished within ``_RIVALRY_THRESHOLD`` points
    of the same rival in ≥ 2 of the last 3 completed weeks.

    Uses the ``recap_log`` cooldown keyed on both player IDs so it fires at
    most once per day per pair — submitting multiple games shouldn't spam.
    """
    monday_now, _ = week_bounds(today)

    # Collect the 3 most-recent Mon–Sun blocks strictly before this week.
    completed_weeks: List[Tuple[date, date]] = []
    cursor = monday_now
    for _ in range(3):
        cursor = cursor - timedelta(weeks=1)
        completed_weeks.append((cursor, cursor + timedelta(days=6)))

    # Build {player_id: points}, {player_id: submissions}, and {player_id: name}
    # per completed week. Submissions are used to gate rivalry detection: a
    # week only counts as "close" when both players played the same number of
    # games — otherwise the comparison is unfair (one player simply played more).
    week_maps: List[Dict[int, float]] = []
    week_subs_maps: List[Dict[int, int]] = []
    name_map: Dict[int, str] = {}  # accumulated across weeks
    for mon, sun in completed_weeks:
        try:
            week_scores = repo.list_scores(
                date_from=mon, date_to=sun, group_id=group_id
            )
        except Exception:
            logger.exception(
                "rivalry: list_scores failed (week %s) — skipping", mon
            )
            week_maps.append({})
            week_subs_maps.append({})
            continue
        game_keys = list(settings.enabled_games) if settings.enabled_games else []
        if game_keys:
            week_scores = [s for s in week_scores if s.game in game_keys]
        lb = weekly_leaderboard(week_scores)
        wm: Dict[int, float] = {}
        sm: Dict[int, int] = {}
        for entry in lb:
            pid = entry.player_id
            wm[pid] = entry.total_points
            sm[pid] = entry.submissions
            if pid not in name_map:
                name_map[pid] = entry.player_name
        week_maps.append(wm)
        week_subs_maps.append(sm)

    # Find this player's points and submissions in each week.
    player_pts_by_week: List[Optional[float]] = [
        wm.get(player_id) for wm in week_maps
    ]
    player_subs_by_week: List[Optional[int]] = [
        sm.get(player_id) for sm in week_subs_maps
    ]

    # For each rival, count weeks where gap ≤ threshold AND both players
    # played the same number of games (so the comparison is apples-to-apples).
    rival_stats: Dict[int, Tuple[int, float]] = {}  # rival_id → (close_weeks, total_gap)
    for week_idx, wm in enumerate(week_maps):
        my_pts = player_pts_by_week[week_idx]
        if my_pts is None:
            continue
        my_subs = player_subs_by_week[week_idx]
        sm = week_subs_maps[week_idx]
        for rival_id, rival_pts in wm.items():
            if rival_id == player_id:
                continue
            # Only compare weeks where both players played the same number of
            # games — if one player played more, the points aren't comparable.
            rival_subs = sm.get(rival_id)
            if rival_subs is None or rival_subs != my_subs:
                continue
            gap = abs(my_pts - rival_pts)
            if gap > _RIVALRY_THRESHOLD:
                continue
            if rival_id not in rival_stats:
                rival_stats[rival_id] = (0, 0.0)
            close_weeks, total_gap = rival_stats[rival_id]
            rival_stats[rival_id] = (close_weeks + 1, total_gap + gap)

    if not rival_stats:
        return None

    # Require ≥ 2 close weeks.
    candidates = {
        rid: stats for rid, stats in rival_stats.items() if stats[0] >= 2
    }
    if not candidates:
        return None

    # Pick closest rival: most close weeks, tie-break by avg gap.
    best_rival_id = min(
        candidates,
        key=lambda rid: (-candidates[rid][0], candidates[rid][1] / candidates[rid][0]),
    )
    close_weeks, total_gap = candidates[best_rival_id]
    avg_gap = total_gap / close_weeks
    rival_name = name_map.get(best_rival_id, "—")

    # Cooldown: fire at most once per day per pair.
    cooldown_key = f"rivalry:{min(player_id, best_rival_id)}:{max(player_id, best_rival_id)}"
    try:
        if repo.has_recap_been_sent(today, cooldown_key, group_id=group_id):
            return None
        repo.mark_recap_sent(today, cooldown_key, group_id=group_id)
    except Exception:
        logger.exception("rivalry: cooldown check failed — skipping trigger")
        return None

    return Trigger(
        kind="rivalry",
        format_data={
            "rival": rival_name,
            "weeks": str(close_weeks),
            "gap": f"{avg_gap:.1f}",
        },
    )


def gather_submission_triggers(
    repo: Repository,
    *,
    player_id: int,
    player_name: str = "",
    game: str,
    new_raw: int,
    today: date,
    prior_raws: List[int],
    group_id: int,
    settings: Optional[Settings] = None,
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
        today_scores = repo.list_today_for_game(
            game=game, day=today, group_id=group_id
        )
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
        fastest_top, slowest_top = repo.get_top_extremes_for_game(
            game=game, n=2, group_id=group_id
        )
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

    weekday = today.weekday()
    try:
        fastest_dow_top = repo.get_top_extremes_for_game_dow(
            game=game, weekday=weekday, n=2, group_id=group_id
        )
    except Exception:
        logger.exception(
            "get_top_extremes_for_game_dow failed (game=%s, weekday=%d) — "
            "skipping group DOW record trigger",
            game,
            weekday,
        )
        fastest_dow_top = []
    dow_record = _detect_group_dow_record_trigger(
        fastest_dow_top, new_raw, player_id, weekday
    )
    if dow_record is not None:
        triggers.append(dow_record)

    # Phase D: finer slices of personal history. We pull the player's
    # full game history once and hand the same list to every Phase-D
    # detector — keeps the repo round-trips down to one even when
    # several Phase-D triggers could match.
    try:
        player_game_history = [
            s for s in repo.list_player_scores(player_id, group_id=group_id)
            if s.game == game
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

    # Phase E: ranking & percentile triggers — nth-best in personal history,
    # quartile bands, weekday-quartile, and above-floor-today consolation.
    nth = _detect_nth_best_trigger(prior_raws, new_raw)
    if nth is not None:
        triggers.append(nth)

    quartile = _detect_quartile_trigger(prior_raws, new_raw)
    if quartile is not None:
        triggers.append(quartile)

    dow_q = _detect_dow_quartile_trigger(player_game_history, new_raw, today)
    if dow_q is not None:
        triggers.append(dow_q)

    above_floor = _detect_above_floor_today_trigger(today_scores, new_raw, player_id)
    if above_floor is not None:
        triggers.append(above_floor)

    # Phase F: social / cross-player triggers — rivalry + podium streak.
    if settings is not None:
        try:
            rivalry = _detect_rivalry_trigger(repo, player_id, group_id, today, settings)
            if rivalry is not None:
                triggers.append(rivalry)
        except Exception:
            logger.exception(
                "rivalry detector failed (player=%s) — skipping", player_id
            )
        try:
            ps = _detect_podium_streak_trigger(
                repo, player_id, player_name, group_id, today,
                settings.enabled_games,
            )
            if ps is not None:
                triggers.append(ps)
        except Exception:
            logger.exception(
                "podium_streak detector failed (player=%s) — skipping", player_id
            )

    return triggers


# ---------------------------------------------------------------------------
# Group broadcasts: photo finish + comeback
# ---------------------------------------------------------------------------

_PHOTO_FINISH_TEMPLATES = (
    "🏁 Photo finish — {p1} leads {p2} by {gap} pts with one {game} left to play.",
    "📸 One {game} to go. {p1} vs {p2}: gap is just {gap} pts. This one's going down to the wire.",
    "⚡ It's neck and neck! {p1} and {p2} are {gap} pts apart — {game} decides it.",
    "🎯 Last game: {game}. {p1} holds a {gap} pt lead over {p2}. Could go either way.",
    "🔥 {p1} and {p2} are {gap} pts apart. One {game} left. Don't blink.",
    "🏁 {p1} is {gap} pts ahead of {p2} — with {game} still to play. Not over yet.",
    "📍 Final game: {game}. The gap between {p1} and {p2} is just {gap} pts.",
)

_COMEBACK_TEMPLATES = (
    "⚡ Comeback alert! {name} just moved from outside the top 2 to first place.",
    "🔄 Plot twist: {name} has taken the lead! {prior_leader} held it for {days_held} {day_word}.",
    "🚀 {name} just snatched the lead from {prior_leader}. {days_held} {day_word} of domination ended.",
    "📈 {name} is back on top. {prior_leader} had led for {days_held} {day_word}.",
    "⚡ {name} just jumped to first. {prior_leader}'s {days_held}-{day_word} lead is gone.",
    "🏆 Leaderboard flip! {name} takes #1. {prior_leader} drops.",
    "🔥 {name} climbed to the top! {prior_leader} was leading for {days_held} {day_word}.",
)


def _photo_finish_message(
    p1_name: str,
    p2_name: str,
    gap: float,
    game: str,
) -> str:
    idx = hash((p1_name, p2_name, game)) % len(_PHOTO_FINISH_TEMPLATES)
    return _PHOTO_FINISH_TEMPLATES[idx].format(
        p1=p1_name, p2=p2_name,
        gap=f"{gap:.1f}",
        game=GAME_DISPLAY.get(game, game),
    )


def _comeback_message(
    name: str,
    prior_leader: str,
    days_held: int,
) -> str:
    idx = hash((name, prior_leader, days_held)) % len(_COMEBACK_TEMPLATES)
    day_word = "day" if days_held == 1 else "days"
    return _COMEBACK_TEMPLATES[idx].format(
        name=name,
        prior_leader=prior_leader,
        days_held=days_held,
        day_word=day_word,
    )


def maybe_broadcast_photo_finish(
    repo: Repository,
    settings: Settings,
    *,
    group_id: int,
    today: date,
    group_recap_to: Optional[str],
    enabled_games: FrozenSet[str],
) -> Optional[str]:
    """Broadcast a photo-finish alert when the top-2 weekly standings are
    within 2.0 pts and exactly one game remains unplayed by both of them
    today. Fires at most once per week."""
    monday, _ = week_bounds(today)
    cooldown_key = f"photo_finish:{monday.isoformat()}"
    try:
        if repo.has_recap_been_sent(today, cooldown_key, group_id=group_id):
            return None
    except Exception:
        logger.exception("photo_finish: cooldown check failed")
        return None

    try:
        week_scores = repo.list_scores(date_from=monday, date_to=today, group_id=group_id)
    except Exception:
        logger.exception("photo_finish: list_scores failed")
        return None

    filtered = [s for s in week_scores if s.game in enabled_games]
    lb = weekly_leaderboard(filtered)
    if len(lb) < 2:
        return None

    top1, top2 = lb[0], lb[1]
    gap = round(top1.total_points - top2.total_points, 1)
    if gap > 2.0:
        return None

    today_scores = [s for s in week_scores if s.puzzle_date == today]
    top2_ids = {top1.player_id, top2.player_id}

    unplayed_by_both = [
        g for g in enabled_games
        if not any(s.game == g and s.player_id in top2_ids for s in today_scores)
    ]
    if len(unplayed_by_both) != 1:
        return None

    game = unplayed_by_both[0]
    body = _photo_finish_message(top1.player_name, top2.player_name, gap, game)
    try:
        repo.mark_recap_sent(today, cooldown_key, group_id=group_id)
    except Exception:
        logger.exception("photo_finish: mark_recap_sent failed")
        return None

    dm_targets: List[str] = []
    try:
        dm_targets = repo.list_active_whatsapp_ids(
            date_from=monday, date_to=today, group_id=group_id
        )
    except Exception:
        logger.exception("photo_finish: list_active_whatsapp_ids failed")

    send_recap(settings, body, dm_targets=dm_targets, group_recap_to=group_recap_to)
    return body


def maybe_broadcast_comeback(
    repo: Repository,
    settings: Settings,
    *,
    player_id: int,
    player_name: str,
    group_id: int,
    today: date,
    group_recap_to: Optional[str],
    enabled_games: FrozenSet[str],
) -> Optional[str]:
    """Broadcast a comeback alert when a player moves from 3rd+ to 1st,
    wasn't in 1st yesterday, and ≥2 enabled games are still unplayed this week."""
    monday, sunday = week_bounds(today)
    cooldown_key = f"comeback:{player_id}:{monday.isoformat()}"
    try:
        if repo.has_recap_been_sent(today, cooldown_key, group_id=group_id):
            return None
    except Exception:
        logger.exception("comeback: cooldown check failed")
        return None

    try:
        week_scores = repo.list_scores(date_from=monday, date_to=today, group_id=group_id)
    except Exception:
        logger.exception("comeback: list_scores failed")
        return None

    filtered = [s for s in week_scores if s.game in enabled_games]
    lb = weekly_leaderboard(filtered)
    if not lb or lb[0].player_id != player_id:
        return None  # not in first

    # Count games with at least one submission anywhere this week.
    games_played_this_week = {s.game for s in filtered}
    games_remaining = enabled_games - games_played_this_week
    if len(games_remaining) < 2:
        return None

    # Check yesterday's standings.
    yesterday = today - timedelta(days=1)
    prior_filtered = [s for s in filtered if s.puzzle_date < today]
    prior_lb = weekly_leaderboard(prior_filtered)
    if prior_lb and prior_lb[0].player_id == player_id:
        return None  # was already 1st yesterday

    prior_leader = prior_lb[0].player_name if prior_lb else None
    if prior_leader is None:
        return None

    # Days the prior leader held the top spot.
    days_held = (today - monday).days  # days into the week

    body = _comeback_message(player_name, prior_leader, max(days_held, 1))
    try:
        repo.mark_recap_sent(today, cooldown_key, group_id=group_id)
    except Exception:
        logger.exception("comeback: mark_recap_sent failed")
        return None

    dm_targets: List[str] = []
    try:
        dm_targets = repo.list_active_whatsapp_ids(
            date_from=monday, date_to=today, group_id=group_id
        )
    except Exception:
        logger.exception("comeback: list_active_whatsapp_ids failed")

    send_recap(settings, body, dm_targets=dm_targets, group_recap_to=group_recap_to)
    return body


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
    for key in ("prior_holder", "weekday", "year", "rank", "rival", "weeks", "gap"):
        if key in fd:
            subs[key] = fd[key]
    # podium_streak uses {weeks} which is already handled above
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


# Trigger kinds whose copy is written in the present tense — "best of
# the day", "first on the board today". Correct for a live submission,
# nonsense for one pasted in three days late, so the backdated path
# drops them and falls back to the day-agnostic triggers (PB, all-time
# record, weekday best…).
_LIVE_DAY_TRIGGER_KINDS = frozenset(
    {"best_of_day", "worst_of_day", "first_today", "above_floor_today"}
)


def maybe_notify_personal_best(
    repo: Repository,
    settings: Optional[Settings],
    *,
    player: Player,
    game: str,
    new_raw: int,
    today: Optional[date] = None,
    deliver: bool = True,
    same_day: bool = True,
    group_id: int,
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

    ``same_day`` is ``False`` for a backdated submission (a share
    pasted in days after the puzzle it belongs to). The detectors are
    all anchored on ``today`` so they stay correct either way, but the
    present-tense triggers in :data:`_LIVE_DAY_TRIGGER_KINDS` would
    read as if the round were still live — those are dropped.
    """
    try:
        all_scores = repo.list_player_scores(player.id, group_id=group_id)
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
        player_name=player.display_name,
        game=game,
        new_raw=new_raw,
        today=today,
        prior_raws=game_raws,
        group_id=group_id,
        settings=settings,
    )
    if not same_day:
        triggers = [
            t for t in triggers if t.kind not in _LIVE_DAY_TRIGGER_KINDS
        ]
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
    group_id: int,
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

    today_scores = repo.list_scores(
        date_from=today, date_to=today, group_id=group_id
    )
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
    week_scores = repo.list_scores(
        date_from=monday, date_to=sunday, group_id=group_id
    )

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
