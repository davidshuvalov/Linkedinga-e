"""Extra message copy, appended to the template pools across the app.

Every pool here is concatenated onto the matching pool in
:mod:`app.notifications`, :mod:`app.jobs` or :mod:`app.scheduler`, so
the renderers don't change — they just have far more lines to draw
from. Each tuple may only use the placeholders its target pool's
renderer supplies; ``tests/test_message_bank.py`` formats every line
against those placeholders so a typo fails CI rather than a DM.

Placeholders by pool are noted above each tuple.
"""

from __future__ import annotations

from typing import Tuple

# ---------------------------------------------------------------------------
# Submission triggers (app.notifications)
# ---------------------------------------------------------------------------

# {name} {game} {new} {prior}
NEW_PB: Tuple[str, ...] = (
    "{name}, new {game} PB: {new}. The old one ({prior}) has been asked to leave the building.",
    "Personal best alert: {name} does {game} in {new}. {prior} is now a museum piece.",
    "{new} on {game}, {name}. That's your best ever. {prior} never stood a chance.",
    "{name} just rewrote their own {game} record: {new} (was {prior}). Frame it.",
    "Out with {prior}, in with {new}. New {game} PB for {name}.",
    "{name}, you've never done {game} better: {new}. Previous best {prior}, now a footnote.",
    "PB! {name} takes {game} in {new}. {prior} called — it wants its dignity back.",
    "{game}: {new}. {name}: unstoppable. Old PB: {prior}. Mood: smug.",
    "{name} beats their own best on {game}. {prior} → {new}. Growth.",
    "New high-water mark for {name} on {game}: {new}. {prior} has been retired with honours.",
    "That's a career-best {game} for {name}: {new}. {prior} is crying somewhere.",
    "{name}, {new} on {game}. Fastest you've ever been. Tell everyone. We will. Old best: {prior}.",
    "Lifetime best {game} for {name}: {new}. The {prior} era is over.",
    "{name} has entered a new {game} tier: {new}. {prior} was the old ceiling.",
    "Stop the press: {name} PBs on {game} with {new} (previous {prior}).",
    "{name}, your {game} PB went from {prior} to {new}. Who are you and what did you do with the old you?",
    "{new} on {game}. {name}'s personal best. The bar moved. The bar is now {new}. Old best: {prior}.",
    "New {game} PB, {name}: {new}. Somewhere, {prior} is feeling very slow.",
    "{name} on {game}: {new}. Personal best. Screenshot it before LinkedIn changes its mind. Old best: {prior}.",
    "{name}, {prior} was good. {new} is better. New {game} PB.",
    "Best {game} of your life, {name}: {new}. Prior record {prior} has been demoted.",
    "{name} just personally outdid themselves on {game}: {new} beats {prior}.",
    "A {game} PB for {name}: {new}. Brain fully caffeinated, apparently. Old best: {prior}.",
    "{name}, {new} on {game} is your new benchmark. {prior} can go sit with the other has-beens.",
    "There it is. {name}'s best-ever {game}: {new}. Previous: {prior}.",
    "PB secured on {game}, {name}: {new}. Old mark {prior}. New swagger: considerable.",
    "{name} has upgraded their {game} PB from {prior} to {new}. Firmware updated.",
    "{new}. On {game}. By {name}. A personal best. Read it again, slowly. Old best: {prior}.",
    "{name}, your past self ({prior}) just got smoked by your present self ({new}) on {game}.",
    "New {game} PB, {name}: {new}. You may now refer to yourself in the third person. Old best: {prior}.",
    "{name}: {new} on {game}. That's a PB, and frankly a bit show-offy. Old best: {prior}.",
    "Best {game} you've ever posted, {name}: {new}. The {prior} chapter closes.",
    "{name} breaks their {game} personal record: {new} (from {prior}). Applause, please.",
    "{game} PB for {name}. {new}. The previous holder, also {name}, is devastated. Old best: {prior}.",
    "{name}, {new} on {game}. Personal best. Your {prior} self would be proud and slightly jealous.",
    "New personal {game} record: {new}. {name} is in the form of their life. Old best: {prior}.",
    "{name} just set a {game} PB of {new}. {prior} has been moved to the archive.",
    "{game}, {new}, PB. {name}, that's the cleanest one you've ever done. Old best: {prior}.",
    "Never been quicker on {game}, {name}: {new}. Prior best {prior}.",
    "{name}, a new {game} personal best: {new}. Let the record show it was beaten fair and square ({prior}).",
    "{name} tops their own {game} chart: {new}. {prior} slides down to number two.",
    "PB on {game}, {name}! {new}. Tomorrow's job: do it again. Old best: {prior}.",
    "{name}, {game} in {new}. Your old best was {prior}. Your new best is showing off.",
    "{name} edges past their own {game} record: {new} vs {prior}. Progress is progress.",
    "Fresh {game} PB for {name}: {new}. Hydrate. Stretch. Gloat. Old best: {prior}.",
    "{name}, {new} on {game} is the best you've ever done. Even the puzzle looks impressed. Old best: {prior}.",
    "New {game} personal best, {name}: {new}. {prior} has left the chat.",
    "{name} vs {name}-from-the-past on {game}: {new} beats {prior}. Present day wins.",
    "{name}, that {new} on {game} is a PB. The group has been notified. Well, you have. Old best: {prior}.",
    "{game} PB! {name} posts {new}, eclipsing {prior}. Big day.",
)

# {name} {game} {new}
TIED_PB: Tuple[str, ...] = (
    "{name}, {new} on {game} equals your PB. So close to a new one you could taste it.",
    "Matched your best {game} exactly, {name}: {new}. Consistency at the top.",
    "{name} ties their own {game} record at {new}. The ceiling held. Barely.",
    "{new} on {game}, {name}. Same as your PB. Not a new record, but the old one is nervous.",
    "PB equalled on {game}: {new}. {name}, you're knocking on the door.",
    "{name}, you hit your {game} best again: {new}. Lightning, meet same place.",
    "Joint personal best on {game} for {name}: {new}. Two of them now.",
    "{name} matches their {game} PB to the second: {new}. Eerie.",
    "{game}: {new}. Exactly your best, {name}. You are a machine. A predictable one.",
    "{name}, {new} on {game} ties your PB. A dead heat with yourself.",
    "Same {game} best, different day: {new}, {name}. Now go one better.",
    "{name} equals their all-time {game} best ({new}). Nearly broke it. Nearly.",
    "Your {game} PB just got company, {name}: another {new}.",
    "{name}, {new} on {game}. Tied PB. The record stands, but it's sweating.",
    "Copy-paste PB on {game}, {name}: {new}. Impressive. Also slightly suspicious.",
    "{name} posts {new} on {game}, level with their best. A photo finish with history.",
    "Tied your {game} personal best, {name}. {new} again. You know what to do tomorrow.",
    "{name}, that's your best {game} time, twice now: {new}.",
    "{new} on {game} — {name} equals their PB. Shared podium, one person.",
    "{name}, matched your {game} record at {new}. The next one is the breakthrough. Probably.",
    "Personal-best tie for {name} on {game}: {new}. The universe is rounding down.",
    "{name} lands exactly on their {game} PB: {new}. Precision engineering.",
    "{game} {new}, {name}. Same as your best. Same glory, half the novelty.",
    "{name}, equalling your {game} best ({new}) still counts as being brilliant.",
    "A matched PB on {game}: {new}. {name}, the record is now jointly held by you and you.",
)

# {name} {game} {new} {prior}
NEW_WORST: Tuple[str, ...] = (
    "{name}, {new} on {game}. That's your slowest ever. {prior} has been relieved of duty.",
    "New personal worst on {game}, {name}: {new}. The old low ({prior}) looks quite good now.",
    "{name} has found a new {game} basement: {new}. Previously {prior}.",
    "{new} on {game}. {name}'s worst ever. Was it a long phone call? It was a long phone call. Old worst: {prior}.",
    "{name}, {game} in {new}. Personal worst. {prior} sends its condolences.",
    "Historic low for {name} on {game}: {new}. The previous worst was {prior}. Was.",
    "{name} just set a {game} record nobody wants: {new} (old worst {prior}).",
    "Your slowest {game} ever, {name}: {new}. Did the puzzle fight back? Old worst: {prior}.",
    "{name}, {new} on {game} beats your previous worst ({prior}). Wrong direction, mate.",
    "Personal worst, {name}: {game} in {new}. Everyone has one. Now you have a new one. Old worst: {prior}.",
    "{game}: {new}. {name}'s worst to date. The puzzle would like a word about its victory. Old worst: {prior}.",
    "{name} out-slowed themselves on {game}: {new} vs {prior}. A dubious milestone.",
    "{new} on {game}, {name}. A personal worst. We're not angry, just disappointed. Old worst: {prior}.",
    "New {game} low for {name}: {new}. {prior} thought it was safe forever.",
    "{name}, your {game} worst moved from {prior} to {new}. Character development.",
    "That {game} ({new}) is the slowest you've ever been, {name}. Did you play it with your elbows? Old worst: {prior}.",
    "{name}'s {game} rock bottom just got a lower floor: {new}. Old worst: {prior}.",
    "Personal worst on {game}: {new}. {name}, it's fine. It's not fine. It's fine. Old worst: {prior}.",
    "{name}, {new} on {game}. Worst ever. Tomorrow, the only way is up. Old worst: {prior}.",
    "{name} has dug past {prior} to reach a new {game} low of {new}. Impressive excavation.",
    "{game}, {new}: a new personal worst for {name}. Put the phone down and walk it off. Old worst: {prior}.",
    "{name}, that's the slowest {game} in your history: {new}. History will be kind. We won't. Old worst: {prior}.",
    "Lifetime-worst {game} for {name}: {new}. The old worst ({prior}) is relieved.",
    "{name} on {game}: {new}. Personal worst. Were you also cooking dinner? Old worst: {prior}.",
    "New {game} anti-PB for {name}: {new}. Previous anti-PB {prior}.",
    "{name}, {new} on {game}. Not a typo. Your worst ever. Old worst: {prior}.",
    "{name}'s {game} worst has been updated to {new}. Please update your expectations accordingly. Old worst: {prior}.",
    "{game} has claimed {name}: {new}, a new personal worst. Old worst: {prior}.",
    "The slowest {name} has ever been on {game}: {new}. {prior} is no longer the low point.",
    "{name}, {new}? On {game}? A personal worst. We'll pretend we didn't see it. Old worst: {prior}.",
    "Worst-ever {game} for {name}: {new}. Somewhere a speedrunner wept. Old worst: {prior}.",
    "{name} sets a new personal floor on {game}: {new}. The floor is lava. Old worst: {prior}.",
    "{new} on {game}, {name}. Your previous worst was {prior}. The trend is not your friend.",
    "{name}, that {game} ({new}) goes straight to the bottom of your record book. Old worst: {prior}.",
    "New personal worst on {game}, {name}. {new}. A brave, brave effort. Old worst: {prior}.",
    "{name} took {new} on {game}. That's a new low. The puzzle is telling all its friends. Old worst: {prior}.",
    "{name}, {prior} used to be your worst {game}. Then {new} happened.",
    "Personal-worst {game} for {name}: {new}. Silver lining: nowhere to go but faster. Old worst: {prior}.",
    "{name}'s slowest-ever {game}: {new}. Did the grid get bigger? The grid did not get bigger. Old worst: {prior}.",
    "{game}: {new}, {name}. Worst ever. Even the timer looked bored. Old worst: {prior}.",
    "{name}, your {game} has hit a new low ({new}). Low tide. It'll come back in. Old worst: {prior}.",
    "That {game} was {name}'s worst yet: {new}. {prior} sends its regards from higher up.",
    "{name}, {new} on {game} — personal worst. At least you finished. You did finish, right? Old worst: {prior}.",
    "New {game} personal worst for {name}: {new}. A learning experience, as they say. Old worst: {prior}.",
    "{name} breaks their worst {game} record: {new} vs {prior}. Wrong record, but a record.",
)

# {name} {game} {new}
TIED_WORST: Tuple[str, ...] = (
    "{name}, {new} on {game} ties your personal worst. Consistently inconsistent.",
    "Equal-worst {game} for {name}: {new}. The basement has two tenants now.",
    "{name} matches their slowest {game} ever: {new}. At least it's not a new low.",
    "{new} on {game}, {name}. Same as your worst. Déjà vu, but slower.",
    "Tied personal worst on {game}, {name}: {new}. The floor held.",
    "{name}, {game} in {new}. Joint-worst. Joint-worst is still technically not the worst.",
    "{name} revisits their {game} low point: {new}. Nice to see old friends.",
    "Your worst {game}, again, {name}: {new}. Tradition is important.",
    "{game}: {new}. {name} equals their personal worst. Steady at the bottom.",
    "{name}, that {new} on {game} ties your worst. Didn't break it though. Silver lining.",
    "Joint personal-worst {game} for {name}: {new}. A rerun nobody asked for.",
    "{name} has matched their {game} rock bottom: {new}. Not lower. Just as low.",
    "Same slowest {game} as ever, {name}: {new}. The low point is now a plateau.",
    "{name}, {new} on {game} — equal worst. The worst is consistent, at least.",
    "{game} {new} for {name}. Ties the worst. The puzzle has a favourite victim.",
    "{name} hits the same {game} floor again: {new}. Maybe it's load-bearing.",
    "Equalled your slowest {game}, {name}: {new}. Not ideal. Not new either.",
    "{name}, {new} on {game}. Tied worst. We've been here before. We know the way out.",
    "{name}'s {game} worst gets a twin: {new}.",
    "{new} on {game}. Same as {name}'s slowest ever. History rhymes.",
)

# {name} {game} {new}
BEST_OF_DAY: Tuple[str, ...] = (
    "{name} leads today's {game} with {new}. Everyone else is chasing.",
    "Top of today's {game} board: {name}, {new}. Come and get it.",
    "{new} on {game} — the best anyone's done today. Take a bow, {name}.",
    "{name}, fastest {game} of the day so far: {new}.",
    "Today's {game} pace-setter: {name} at {new}.",
    "{name} is today's {game} benchmark: {new}. Good luck, everyone else.",
    "Nobody's beaten {name}'s {new} on {game} today. Yet.",
    "{game} leader for today: {name}, {new}. The rest of you have homework.",
    "{name} grabs the {game} lead with {new}. The crown is temporary. Enjoy it.",
    "Fastest {game} today: {new}, by {name}. Bragging rights unlocked.",
    "{name}, {new} on {game} puts you top of the pile today.",
    "{name} has the day's best {game}: {new}. Others, consider yourselves warned.",
    "Today's {game} is {name}'s to lose: {new}.",
    "{new} on {game}. Best of the day. {name}, don't get comfortable.",
    "{name} sits on top of today's {game} with {new}. Lovely view up there.",
    "Pole position on today's {game}: {name}, {new}.",
    "{name}, you're leading {game} today with {new}. The group chat trembles.",
    "Best {game} in the group today: {name}'s {new}.",
    "{name} sets today's {game} high bar: {new}. Mind the bar.",
    "{name}, {new} is the time to beat on {game} today.",
    "{game} crown, today's edition: {name}, {new}.",
    "{name} out in front on today's {game}: {new}. Everyone's looking at your back.",
    "Daily {game} leader: {name}. Time: {new}. Smugness: earned.",
    "{name} owns today's {game} so far: {new}.",
    "{new} on {game} is the best today. {name}, it's your board now.",
    "{name}, today's quickest {game}: {new}. Others have until midnight LA to do something about it.",
    "The {game} lead goes to {name} at {new}. Hold on tight.",
    "{name} is top of today's {game} with {new}. The chasers are lacing up.",
    "{name}, best {game} of the day: {new}. Officially the one to beat.",
    "Today's {game} gold: {name}, {new}.",
    "{name} grabs today's {game} lead with {new}. Leave some for the rest.",
    "Fastest hands on {game} today: {name}, {new}.",
    "{name}, {new} on {game} leads the field. The field is grumbling.",
    "{game} today: {name} {new}, everyone else behind.",
    "{name} has today's best {game}. {new}. That's the number now.",
    "Leaderboard update: {name} takes today's {game} with {new}.",
    "{name}, you're the {game} pace car today: {new}.",
    "Top {game} today belongs to {name}: {new}. For now.",
    "{name} with today's best {game} ({new}). The others have been put on notice.",
    "{new} — the day's best {game}. {name}, magnificent.",
    "Nobody's touched {name} on {game} today: {new}.",
    "{name} leads the {game} pack with {new}. Wolves, catch up.",
    "{name}, today's {game} champion so far: {new}.",
    "The {game} number of the day is {new}, courtesy of {name}.",
    "{name} has the best {game} today. {new}. Everyone else is playing for silver.",
    "Today's {game} high score holder (well, low score): {name}, {new}.",
    "{name} puts {new} on the {game} board. Nobody's beaten it today.",
    "{game} lead change: {name} now top with {new}.",
    "{name}, best {game} today with {new}. The throne is comfy, isn't it.",
    "{name}'s {new} leads today's {game}. Respect.",
)

# {name} {game} {new}
WORST_OF_DAY: Tuple[str, ...] = (
    "{name}, {new} on {game} is the slowest in the group today. Someone has to be.",
    "Today's {game} wooden spoon goes to {name}: {new}.",
    "{name} brings up the rear on today's {game}: {new}.",
    "{new} on {game}, {name}. Last place today. The view from the back is scenic.",
    "{name}, slowest {game} of the day so far: {new}. Plenty of time for someone to undercut you.",
    "Bottom of today's {game} board: {name}, {new}.",
    "{name} is today's {game} anchor: {new}. Anchors are important. Heavy, but important.",
    "{game} today: everyone is ahead of {name}'s {new}.",
    "{name}, {new} is the time everyone else is beating on {game} today.",
    "Today's slowest {game}: {name}, {new}. A bold strategic choice.",
    "{name} holds the {game} lantern rouge today: {new}.",
    "{name}, last on today's {game} with {new}. Last is just first, backwards.",
    "{new} on {game} — the group's slowest today. {name}, we see you.",
    "{name} props up today's {game} table: {new}.",
    "Tail-end Charlie on {game} today: {name}, {new}.",
    "{name}, {new} on {game} puts you last today. Hope someone has a worse one.",
    "{game} back marker today: {name}. {new}.",
    "{name} is bringing up the {game} rear: {new}. Mind the gap.",
    "Slowest {game} in the group today: {name}'s {new}.",
    "{name}, you're last on {game} so far ({new}). There's hope — not everyone's played.",
    "{name} at {new} on {game}. The rest of the group thanks you for the confidence boost.",
    "Today's {game} cellar dweller: {name}, {new}.",
    "{name}, bottom of the {game} pile today: {new}. Comfy down there?",
    "{new} on {game}. Last today. {name}, this is what we call a rebuilding day.",
    "{name} is the reason everyone else feels good about their {game} today: {new}.",
    "{game} sweeper today: {name}, {new}. Somebody has to tidy up.",
    "{name}, last place on {game} with {new}. The participation trophy is in the post.",
    "{name}'s {new} is today's slowest {game}. Make peace with it.",
    "Wooden spoon, {game} edition: {name} ({new}).",
    "{name}, {new} is today's {game} floor. Everyone else is standing on you.",
    "Last on {game} today, {name}: {new}. Tomorrow's a clean slate.",
    "{name} finishes today's {game} at the back: {new}.",
    "The group's slowest {game} today: {new}. Step forward, {name}.",
    "{name}, {game} in {new}. Bottom of the day so far. Pray for a latecomer.",
    "{name} holds the {game} spoon today with {new}. Stir something with it.",
    "{name} at the back of today's {game} queue: {new}.",
    "{game} today, last place: {name} ({new}). Points for showing up. Not many.",
    "{name}, nobody was slower on {game} today than your {new}.",
    "Today's {game} is a race and {name} is last: {new}.",
    "{new} on {game}, {name}. Slowest today. At least you're on the board.",
    "{name}'s {game} today ({new}) is the group's slowest. Bravely done.",
    "{name} brings the {game} average down today: {new}.",
    "Last place on today's {game}: {name}. {new}. A humble day.",
    "{name}, {new} on {game} — today's slowest. Someone has to make the others look good.",
    "Bottom of the {game} barrel today: {name}, {new}.",
)

# {name} {game} {new} {prior} {prior_holder}
ALL_TIME_RECORD: Tuple[str, ...] = (
    "🏆 GROUP RECORD. {name} does {game} in {new}. {prior_holder}'s {prior} is history.",
    "{name} just set the fastest {game} this group has ever seen: {new}. Bye, {prior_holder} ({prior}).",
    "All-time {game} record: {new}, {name}. {prior_holder} held it at {prior}. Held.",
    "Fastest {game} in group history: {name}, {new}. {prior_holder} is in mourning.",
    "{name} takes the all-time {game} record off {prior_holder}: {new} beats {prior}.",
    "New group record! {game} in {new} by {name}. Previous holder {prior_holder} ({prior}).",
    "{new} on {game}. Nobody in this group has ever done better. {name}, legend.",
    "The {game} record book has a new first page: {name}, {new}. ({prior_holder}: {prior}.)",
    "{name} dethrones {prior_holder} for the all-time {game} record: {new} vs {prior}.",
    "Record broken. {game}: {new}. {name} now holds the all-time best. {prior_holder}, it's over.",
    "{name}, {new} on {game} is the best this group has ever posted. Pin it.",
    "All-time {game} monarch: {name}, {new}. Deposed: {prior_holder} ({prior}).",
    "{name} has the fastest {game} in recorded history (this group's, anyway): {new}.",
    "History made on {game}: {name}, {new}. {prior_holder}'s {prior} is now second best ever.",
    "{name} smashes the group {game} record: {new}. {prior_holder}, you had a good run.",
    "{game} all-time record: {name}, {new}. Somebody tell {prior_holder} gently.",
    "Group-best {game} ever: {new}, by {name}. The old mark was {prior} by {prior_holder}.",
    "{name}, you've just done the fastest {game} anyone here has ever done. {new}.",
    "New all-time {game} record: {new}. {name} takes it from {prior_holder} ({prior}).",
    "The {game} record falls! {name}: {new}. {prior_holder}: {prior}. Yesterday's news.",
)

# {name} {game} {new} {prior} {prior_holder}
ALL_TIME_ANTI_RECORD: Tuple[str, ...] = (
    "The slowest {game} in group history: {name}, {new}. {prior_holder}'s {prior} is off the hook.",
    "{name} has set the all-time slowest {game}: {new}. {prior_holder} ({prior}) is thrilled.",
    "Historic. Not good, but historic. {name}'s {game} in {new} is the group's slowest ever.",
    "{name}, {new} on {game}. Nobody here has ever taken longer. {prior_holder} is free.",
    "Anti-record alert: {game}, {new}, {name}. Previous slowest: {prior_holder} ({prior}).",
    "{name} relieves {prior_holder} of the slowest-{game}-ever title: {new} vs {prior}.",
    "The group's {game} hall of shame has a new top entry: {name}, {new}.",
    "{name}, {game} in {new}. That's the slowest this group has ever recorded. Impressive, in a way.",
    "All-time slowest {game}: {new} by {name}. {prior_holder}'s {prior} looks positively rapid now.",
    "New group low on {game}: {name}, {new}. {prior_holder} sends thanks.",
    "{name} owns the slowest {game} in history: {new}. A legacy of sorts.",
    "Group-worst {game} ever: {new}. {name}, you've taken it from {prior_holder} ({prior}).",
    "{name}'s {new} on {game} breaks the group's slowest record. Was there a fire drill?",
    "{game} anti-record: {name}, {new}. {prior_holder} can finally sleep at night.",
    "The longest {game} this group has ever seen: {name}, {new}.",
    "{name} has made {game} history: slowest ever, {new}. Previous: {prior_holder}, {prior}.",
    "Record broken, wrong end: {name}, {game}, {new}. Sorry, {prior_holder}, it's not yours anymore.",
    "{name}, the all-time slowest {game} belongs to you now: {new}.",
    "{game}: {new}. Slowest in group history. {name} takes over from {prior_holder} ({prior}).",
    "Every group needs a slowest-ever. For {game}, it's now {name}: {new}.",
)

# {name} {game} {new} {prior} {prior_holder} {weekday}
GROUP_DOW_RECORD: Tuple[str, ...] = (
    "Best {weekday} {game} this group has ever seen: {name}, {new}. ({prior_holder}: {prior}.)",
    "{name} owns {weekday} {game} now: {new}. {prior_holder}'s {prior} is retired.",
    "{weekday} {game} record: {name}, {new}. Previously {prior_holder} at {prior}.",
    "Nobody's done a {weekday} {game} faster: {name}, {new}. Previous record: {prior_holder}, {prior}.",
    "{name} takes the all-time {weekday} {game} crown from {prior_holder}: {new} vs {prior}.",
    "Group record for {weekday}s on {game}: {new}. Take a bow, {name}. Previous record: {prior_holder}, {prior}.",
    "{weekday} {game}, all-time best: {name}, {new}. {prior_holder} held it at {prior}.",
    "{name}, the fastest {weekday} {game} in group history: {new}. Previous record: {prior_holder}, {prior}.",
    "{weekday}s belong to {name} now. {game}: {new}. Old record {prior} ({prior_holder}).",
    "All-time {weekday} best on {game}: {new} by {name}. {prior_holder} is gutted. Old mark: {prior}.",
    "{name} breaks the {weekday} {game} record: {new}. {prior_holder}'s {prior}: superseded.",
    "{weekday} {game} history rewritten: {name}, {new}. Previous record: {prior_holder}, {prior}.",
    "{name}, best {weekday} {game} ever posted here: {new}. Previous best {prior}, by {prior_holder}.",
    "New {weekday} {game} group record: {name}, {new}. Sorry, {prior_holder}. Old mark: {prior}.",
    "The {weekday} {game} record has a new name on it: {name}, {new}. Previous record: {prior_holder}, {prior}.",
)

# {name} {game} {new} {prior} {weekday}
DOW_PB: Tuple[str, ...] = (
    "{name}, best {weekday} {game} you've ever done: {new} (was {prior}).",
    "{weekday} {game} PB for {name}: {new}. {weekday}s suit you. Old {weekday} best: {prior}.",
    "Your fastest {weekday} {game}, {name}: {new}. Previous {weekday} best {prior}.",
    "{name} on a {weekday}: {game} in {new}. Best {weekday} ever. Old {weekday} best: {prior}.",
    "{weekday} personal best on {game}, {name}: {new}. Old {weekday} best: {prior}.",
    "{name}, {new} on {game} — your best {weekday} yet. Old {weekday} best {prior}.",
    "{weekday} {game} record for {name}: {new}. {weekday}s are looking up. Old {weekday} best: {prior}.",
    "Best {game} you've ever had on a {weekday}, {name}: {new}. Old {weekday} best: {prior}.",
    "{name} owns their {weekday}s now: {game}, {new} (was {prior}).",
    "New {weekday} {game} PB, {name}: {new}. Mark the calendar. Old {weekday} best: {prior}.",
    "{name}, {weekday} {game} at {new}. Your {weekday} best. {prior} retired.",
    "Fastest {weekday} {game} of your career, {name}: {new}. Old {weekday} best: {prior}.",
    "{weekday} PB! {name}, {game}, {new}. Previous {prior}.",
    "{name}'s {weekday}s just got faster: {game} in {new}. Old {weekday} best: {prior}.",
    "{name}, best ever {weekday} on {game}: {new}. {weekday} is officially your day. Old {weekday} best: {prior}.",
    "{weekday} {game}: {new}. {name}'s best for this day of the week. {prior} demoted.",
    "{name} set a {weekday} {game} PB: {new}. Other {weekday}s, take notes. Old {weekday} best: {prior}.",
    "{name}, that's your best {weekday} {game}: {new}. The {weekday} curse is lifted. Old {weekday} best: {prior}.",
    "Personal {weekday} record on {game}: {name}, {new}. Old {weekday} best: {prior}.",
    "{name}, {weekday} {game} in {new}. Never done a {weekday} better. Old {weekday} best: {prior}.",
)

# {name} {game} {new} {prior} {weekday}
DOW_WORST: Tuple[str, ...] = (
    "{name}, your slowest {weekday} {game} ever: {new} (was {prior}).",
    "{weekday} {game} personal worst for {name}: {new}. {weekday}s, eh. Old {weekday} worst: {prior}.",
    "{name} has a new {weekday} low on {game}: {new}. Old {weekday} worst: {prior}.",
    "Worst {weekday} {game} you've had, {name}: {new}. Previous {weekday} worst {prior}.",
    "{name}, {new} on {game}. Your slowest {weekday} on record. Old {weekday} worst: {prior}.",
    "{weekday}s are not {name}'s {game} day: {new}, a new {weekday} worst. Old {weekday} worst: {prior}.",
    "{weekday} {game} low for {name}: {new}. Blame the day of the week. We do. Old {weekday} worst: {prior}.",
    "{name}, {weekday} {game} at {new}. Worst {weekday} yet. {prior} was the old floor.",
    "New {weekday} personal worst on {game}, {name}: {new}. Old {weekday} worst: {prior}.",
    "{name}'s {weekday} {game} hit a new low: {new}. {weekday}s are cursed. Probably. Old {weekday} worst: {prior}.",
    "{name}, slowest {weekday} {game} ever: {new}. Maybe skip {weekday}s. (Don't.) Old {weekday} worst: {prior}.",
    "{weekday} worst on {game}: {name}, {new}. The old one ({prior}) was gentler.",
    "{name}, {game} on a {weekday}: {new}. Your worst {weekday} ever. Old {weekday} worst: {prior}.",
    "{weekday} strikes again: {name}'s slowest {weekday} {game}, {new}. Old {weekday} worst: {prior}.",
    "{name}'s {weekday} {game} record, the bad one: {new}. Old {weekday} worst: {prior}.",
)

# {name} {game} {new} {prior} {year}
YEAR_PB: Tuple[str, ...] = (
    "{name}, fastest {game} of {year}: {new} (was {prior}).",
    "{year} best on {game} for {name}: {new}. Previous {year} best: {prior}.",
    "Your best {game} this year, {name}: {new}. {year} is going well. Previous {year} best: {prior}.",
    "{name}, {new} on {game}. Best of {year} so far. Previous {year} best: {prior}.",
    "{year} {game} PB for {name}: {new}. Previous {year} best {prior}.",
    "{name}'s {year} highlight reel adds a {game}: {new}. Previous {year} best: {prior}.",
    "Fastest {game} you've done in {year}, {name}: {new}. Previous {year} best: {prior}.",
    "{name}, {game} in {new}. Best of {year}. {prior} had a good run.",
    "{year} season best on {game}: {name}, {new}. Previous {year} best: {prior}.",
    "{name}, this year's best {game} for you: {new}. {year} peaking. Previous {year} best: {prior}.",
    "New {year} {game} high (low) for {name}: {new}. Previous {year} best: {prior}.",
    "{name}, {new} on {game} is your {year} best. Year-end review: positive. Previous {year} best: {prior}.",
    "{year}'s fastest {game} by {name}: {new}. Old {year} best {prior}.",
    "{name}, best {game} in {year}: {new}. Calendar-worthy. Previous {year} best: {prior}.",
    "{game}, {year} edition: {name} sets a new personal best at {new}. Previous {year} best: {prior}.",
)

# {name} {game} {new}
FIRST_TODAY: Tuple[str, ...] = (
    "{name}, first {game} on the board today: {new}. Everyone else is officially late.",
    "{name} opens today's {game} at {new}. The gauntlet is down.",
    "Early bird alert: {name}, {game}, {new}.",
    "{name} is first through the {game} door today: {new}.",
    "{game} is open for business. First customer: {name}, {new}.",
    "{name}, you've planted today's {game} flag at {new}.",
    "{name} kicks off {game} today with {new}. Your move, everyone.",
    "Day one, entry one: {name}, {game}, {new}.",
    "{name}, first {game} of the day. {new}. The target's painted.",
    "{name} gets the {game} party started: {new}.",
    "First {game} in: {name}, {new}. Everyone else is reading this from behind.",
    "{name} sets today's opening {game} number: {new}.",
    "{name}, the {game} board was empty. Now it says {new}. Your handiwork.",
    "{game} has its first entry today: {name} at {new}.",
    "Who was keen? {name} was keen. {game}: {new}.",
    "{name} breaks today's {game} duck: {new}.",
    "{name}, first {game} posted today: {new}. The early bird gets the leaderboard.",
    "Opening bat for today's {game}: {name}, {new}.",
    "{name} leads off on {game}: {new}. Nobody else has dared yet.",
    "First on {game} today, {name}. {new}. Bold of you to go first.",
    "{name} tees off today's {game}: {new}.",
    "{name}, the first {game} of the day is in, and it's {new}.",
    "{game} today begins with {name}: {new}.",
    "{name} has today's first {game}: {new}. A provisional crown, at least.",
    "{name}, {new} on {game}, posted before anyone else. Keen bean.",
    "Today's {game} has started. {name} went first with {new}.",
    "{name} drops the first {game} of the day: {new}. Let the chasing begin.",
    "{name}, first in on {game} ({new}). Pressure's on the others now.",
    "{name} sets the opening mark on {game}: {new}.",
    "First {game} score of the day: {new}, {name}. Everything else is a response.",
    "{name}, you're leading {game} by default. {new}. Enjoy the lonely summit.",
    "{name} gets the first word on {game} today: {new}.",
    "{name} is today's {game} trailblazer at {new}.",
    "{game} scoreboard, entry #1: {name}, {new}.",
    "{name}, you're first on {game} and therefore top. Mathematically airtight. {new}.",
    "{name} turns the lights on for today's {game}: {new}.",
    "The first {game} of the day is {name}'s: {new}.",
    "{name}, first {game} today at {new}. Now we wait.",
    "{name} has christened today's {game} board with {new}.",
    "Nobody had played {game} until {name} did it in {new}.",
)

# {name} {game} {new} {rank}
NTH_BEST: Tuple[str, ...] = (
    "{name}, {new} on {game} is your {rank} best ever. So close to the top.",
    "Your {rank}-best {game} of all time, {name}: {new}.",
    "{name}, {game} in {new}. That's your {rank} best. The PB is nervous.",
    "{rank} best {game} for {name}: {new}. Top-five material.",
    "{name} posts their {rank} best {game}: {new}. Nearly PB territory.",
    "{new} on {game}, {name}. All-time {rank}. Quietly excellent.",
    "{name}, that's the {rank} best {game} you've ever done: {new}.",
    "{rank} on your all-time {game} list, {name}: {new}.",
    "Not a PB, but your {rank} best {game}: {new}, {name}.",
    "{name}, {new} on {game} goes in at {rank} on your personal chart.",
    "{game}: {new}. {name}'s {rank} best. The hall of fame expands.",
    "{name}'s {rank}-fastest {game} ever: {new}. Fine work.",
    "Personal top list update, {name}: {game}, {new}, enters at {rank}.",
    "{name}, {rank} best {game} of your life: {new}. Podium-ish.",
    "{new} on {game} — {rank} best for {name}. The good stuff.",
    "{name}, {game} in {new} is your {rank} best. The PB can feel your breath.",
    "{rank}-best {game} for {name}: {new}. A very respectable day.",
    "{name}, that {game} ({new}) ranks {rank} in your history.",
    "New entry at {rank} in {name}'s {game} top list: {new}.",
    "{name} nearly hit a PB on {game}: {new}, your {rank} best.",
    "{name}, {new}. {game}. {rank} best ever. Not bad, not bad at all.",
    "{game} {new}: {name}'s {rank} best. Elite company, all of it yours.",
    "{name}, your {rank} quickest {game} ever: {new}.",
    "{rank} place in {name}'s all-time {game} rankings: {new}.",
    "{name}, {new} on {game}. {rank} best. One of the greats.",
)

# {name} {game} {new}
TOP_QUARTILE: Tuple[str, ...] = (
    "{name}, {new} on {game} is in your top 25%. Good day.",
    "Top-quarter {game} for {name}: {new}.",
    "{name}, that's one of your sharper {game} days: {new}.",
    "{new} on {game}. {name}'s good form is showing.",
    "{name}, {game} in {new}. Better than three quarters of your history.",
    "Upper-tier {game} from {name}: {new}.",
    "{name}, {new} on {game}. That's the good version of you.",
    "A top-quartile {game} for {name}: {new}. Solid stuff.",
    "{name}'s {game} today ({new}) sits in their best 25%.",
    "{game}: {new}. {name} is in the zone.",
    "{name}, {new} on {game} — comfortably one of your better ones.",
    "Good {game}, {name}: {new}. Top quarter of your record.",
    "{name} brings their A-game to {game}: {new}.",
    "{new} on {game}, {name}. Historically, that's a strong one.",
    "{name}, upper quartile {game} today: {new}.",
    "{name}'s {game}: {new}. The data says: nice.",
    "{name}, {game} in {new} beats most of your history. Keep it up.",
    "Above your norm and then some, {name}: {game} in {new}.",
    "{name}, that {game} ({new}) belongs in your highlights.",
    "Quality {game} from {name}: {new}. Top 25%.",
    "{name}, {new} on {game}. You're in form.",
    "{name}'s {game} ({new}) is better than 75% of their efforts. Lovely.",
    "{game}, {new}, {name}: one for the good pile.",
    "{name}, that's a top-shelf {game} for you: {new}.",
    "Strong {game} from {name} today: {new}. The good quartile.",
    "{name}, {new} on {game}. Your recent self is doing alright.",
    "{name} puts in a top-quarter {game}: {new}.",
    "{game}: {new}. Top 25% for {name}. Quietly brilliant.",
    "{name}, {new} on {game}. One of your better ones, for sure.",
    "{name}, that {game} is in your best quarter: {new}. Lovely tidy work.",
)

# {name} {game} {new}
BOTTOM_QUARTILE: Tuple[str, ...] = (
    "{name}, {new} on {game} is in your bottom 25%. It happens.",
    "Bottom-quarter {game} for {name}: {new}. Shake it off.",
    "{name}, that's one of your slower {game} days: {new}.",
    "{new} on {game}. Not {name}'s finest work.",
    "{name}, {game} in {new}. Slower than three quarters of your history.",
    "Lower-tier {game} from {name}: {new}. Tomorrow's another puzzle.",
    "{name}, {new} on {game}. The less good version of you turned up.",
    "A bottom-quartile {game} for {name}: {new}. Brush it off.",
    "{name}'s {game} today ({new}) sits in their slowest 25%.",
    "{game}: {new}. {name} is not in the zone. {name} is near the zone. Adjacent.",
    "{name}, {new} on {game} — one of your slower ones.",
    "Off day on {game}, {name}: {new}. Bottom quarter.",
    "{name} brought their B-game to {game}: {new}. Maybe their C-game.",
    "{new} on {game}, {name}. Historically, that's a weak one.",
    "{name}, lower-quartile {game} today: {new}.",
    "{name}'s {game}: {new}. The data says: hmm.",
    "{name}, {game} in {new} — slower than most of your record.",
    "Below your norm on {game}, {name}: {new}.",
    "{name}, that {game} ({new}) belongs in your blooper reel.",
    "Not your day on {game}, {name}: {new}. Bottom 25%.",
    "{name}, {new} on {game}. Even good players have these.",
    "{name}'s {game} ({new}) is slower than 75% of their efforts. Next.",
    "{game}, {new}, {name}: one for the forget pile.",
    "{name}, that's a bottom-shelf {game} for you: {new}.",
    "Weak {game} from {name} today: {new}. The low quartile beckoned.",
    "{name}, {new} on {game}. Your usual self is on holiday.",
    "{name} puts in a bottom-quarter {game}: {new}. Rest up.",
    "{game}: {new}. Bottom 25% for {name}. We move.",
    "{name}, {new} on {game}. One of your slower ones, no doubt about it.",
    "{name}, that {game} is in your worst quarter: {new}. Tomorrow, redemption.",
)

# {name} {game} {new} {weekday}
DOW_TOP_QUARTILE: Tuple[str, ...] = (
    "{name}, {new} on {game} is a top-quarter {weekday} for you.",
    "{weekday}s agree with you, {name}: {game} in {new}.",
    "Strong {weekday} {game}, {name}: {new}.",
    "{name}, {new} on {game} — one of your best {weekday}s.",
    "{weekday} form is good, {name}: {game}, {new}.",
    "{name}'s {weekday} {game} ({new}) is top 25% for the day.",
    "{name}, you do {weekday} {game} well: {new}.",
    "Upper-quartile {weekday} on {game}: {name}, {new}.",
    "{name}, a {weekday} to remember on {game}: {new}.",
    "{game} on a {weekday}, {name}: {new}. Your {weekday}s are strong.",
    "{name}, {weekday} {game} in {new}. A good one for the day.",
    "{weekday} is your {game} day, {name}: {new}.",
)

# {name} {game} {new} {weekday}
DOW_BOTTOM_QUARTILE: Tuple[str, ...] = (
    "{name}, {new} on {game} is a bottom-quarter {weekday} for you.",
    "{weekday}s don't agree with you, {name}: {game} in {new}.",
    "Weak {weekday} {game}, {name}: {new}.",
    "{name}, {new} on {game} — one of your slower {weekday}s.",
    "{weekday} form is shaky, {name}: {game}, {new}.",
    "{name}'s {weekday} {game} ({new}) is bottom 25% for the day.",
    "{name}, {weekday} {game} remains a struggle: {new}.",
    "Lower-quartile {weekday} on {game}: {name}, {new}.",
    "{name}, a {weekday} to forget on {game}: {new}.",
    "{game} on a {weekday}, {name}: {new}. {weekday}s have it in for you.",
    "{name}, {weekday} {game} in {new}. A rough one for the day.",
    "{weekday} is not your {game} day, {name}: {new}.",
)

# {name} {game} {new} {prior} {prior_holder}
ABOVE_FLOOR_TODAY: Tuple[str, ...] = (
    "{name}, {new} on {game} isn't great, but {prior_holder}'s {prior} is worse. Phew.",
    "Slow {game} for {name} ({new}), but {prior_holder} ({prior}) is propping up the table.",
    "{name}, {new} on {game} and you're not last. {prior_holder} is, at {prior}. Silver linings.",
    "{new} on {game} — meh. But {prior_holder} posted {prior}, so it could be worse, {name}.",
    "{name}, thank {prior_holder} for that {prior} on {game}. It makes your {new} look fine.",
    "{name}: {new} on {game}. Mediocre, but above {prior_holder}'s {prior}.",
    "Not great, {name} ({new} on {game}). Not {prior_holder}-at-{prior} bad, though.",
    "{name}, a slow {game} ({new}), but {prior_holder} has the spoon at {prior}.",
    "{prior_holder}'s {prior} on {game} is today's floor. {name}, your {new} is safely above it.",
    "{name}, {new} on {game}. You dodged the wooden spoon. {prior_holder} ({prior}) didn't.",
    "{name}, you'd feel bad about {new} on {game} if {prior_holder} hadn't done {prior}.",
    "{name}: {new}. {prior_holder}: {prior}. On {game}, at least, you're not the worst.",
    "{game}: {name} {new}, {prior_holder} {prior}. Someone's having a worse day than you.",
    "{name}, not your best {game} ({new}), but {prior_holder} is the one getting texts about {prior}.",
    "{name}, cling to this: {prior_holder} did {game} in {prior}. Your {new} is fine. Fine-ish.",
    "Slow-ish {game} for {name}: {new}. Slower still: {prior_holder}, {prior}.",
    "{name}, {new} on {game}. At least {prior_holder} ({prior}) is there to absorb the shame.",
    "{name}, {new} on {game}. You can blame the puzzle. {prior_holder} can't — their {prior} is worse.",
    "{name}, {game} in {new}. Off the pace, but {prior_holder}'s {prior} is even further off.",
    "Close to the bottom on {game}, {name} ({new}), but {prior_holder} ({prior}) has dibs.",
    "{name}, {new} on {game} — {prior_holder}'s {prior} makes you look positively nippy.",
    "Rough {game}, {name}: {new}. Rougher for {prior_holder}: {prior}.",
    "{name}, you beat {prior_holder} on {game}. They did {prior}. You did {new}. Victory is relative.",
    "{name} avoids last on {game} with {new}. {prior_holder} ({prior}) wasn't so lucky.",
    "{name}, {new} on {game} — {prior_holder} will take the heat today at {prior}.",
    "{game} today: {name} {new}. Not good. But {prior_holder}'s {prior}: worse.",
    "{name}, the bottom of today's {game} belongs to {prior_holder} ({prior}). You're just near it ({new}).",
    "{name}, {new} on {game}. If it helps, {prior_holder} took {prior}.",
    "Not the worst {game} today, {name}: {new}. That honour is {prior_holder}'s, at {prior}.",
    "{name}, {new} on {game}. Saved from the spoon by {prior_holder}'s {prior}. Buy them a coffee.",
)

# {name} {rival} {weeks} {gap}
RIVALRY: Tuple[str, ...] = (
    "{name}, you and {rival} have been within {gap} points of each other for {weeks} weeks. It's a rivalry now.",
    "{rival} is your shadow, {name}: {weeks} close weeks, average gap {gap} points.",
    "{name} vs {rival}: {weeks} weeks, {gap} points apart on average. Nobody's backing down.",
    "{name}, {rival} has been breathing down your neck for {weeks} weeks (gap: {gap} pts).",
    "It's {name} vs {rival} again. {weeks} weeks of {gap}-point margins. Pick a side, everyone.",
    "{weeks} weeks, {gap} points average. {name} and {rival} are basically the same person at this point.",
    "{name}, your nemesis has a name: {rival}. {weeks} weeks within {gap} points.",
    "{rival} and {name}: {weeks} tight weeks, {gap} points between them. Get the popcorn.",
    "{name}, {rival} is right there. {weeks} weeks, {gap}-point average gap. Every second counts.",
    "Rivalry watch: {name} vs {rival}. {weeks} weeks, {gap} pts. Neither will admit it matters.",
    "{name}, {rival} has been your closest competitor for {weeks} weeks ({gap} pts). Make it count today.",
    "{weeks} weeks of {name} vs {rival}, average gap {gap}. This is personal now.",
    "{name}, every puzzle is a duel with {rival}: {weeks} weeks, {gap} points apart.",
    "{rival} vs {name}, round {weeks}. Average margin {gap}. Ding ding.",
    "{name}, the {rival} rivalry is real: {weeks} weeks, {gap} points between you on average.",
)

# {name} {weeks}
PODIUM_STREAK: Tuple[str, ...] = (
    "{name}, {weeks} weeks on the podium in a row. You live there now.",
    "Podium regular: {name}, {weeks} straight weeks in the top three.",
    "{weeks} consecutive podium weeks for {name}. Pay rent.",
    "{name}, top three for {weeks} weeks running. Consistency, thy name is {name}.",
    "{name} has made the podium {weeks} weeks in a row. The steps have your footprints in them.",
    "{weeks}-week podium streak, {name}. The bronze, silver and gold all know your name.",
    "{name}, {weeks} weeks without leaving the top three. Remarkable stamina.",
    "Podium again? {name}, that's {weeks} weeks straight.",
    "{name} is a fixture in the top three: {weeks} weeks and counting.",
    "{weeks} weeks on the podium, {name}. Someone get you a plaque.",
    "{name}'s podium streak hits {weeks} weeks. The others are getting fed up.",
    "{name}, {weeks} weeks top three. Is it boring being this good? Asking for the group.",
)

# {name} {game} {new} {usual}
VS_USUAL_BETTER: Tuple[str, ...] = (
    "{name}, {new} on {game} beats your usual {usual}. Good day.",
    "Quicker than normal, {name}: {game} in {new} (usually {usual}).",
    "{name}'s {game}: {new}. Typical {name}: {usual}. Today's {name}: better.",
    "{new} on {game}, {name}. Your median is {usual}. You're above it. Well, below it. Good.",
    "{name}, you normally do {game} in about {usual}. Today: {new}. Nice.",
    "{game} {new}, {name} — sharper than your usual {usual}.",
    "{name} beats their typical {game} ({usual}) with {new}.",
    "{name}, {new} on {game}. Ahead of your normal pace ({usual}).",
    "Better-than-usual {game} from {name}: {new} vs {usual}.",
    "{name}, {game} in {new}. Your normal is {usual}. Today is not normal. Today is good.",
    "{name} is having a good {game} day: {new} (median {usual}).",
    "{new} on {game}. {name}'s usual: {usual}. Improvement noted.",
    "{name}, {game} went well: {new} vs your typical {usual}.",
    "Above-par {game} for {name}: {new}. Normally {usual}.",
    "{name}, {new} on {game}. Quicker than your usual {usual}. Something in the coffee?",
    "{name} outperforms their usual {game} ({usual}): {new}.",
    "{name}, typical {game} for you: {usual}. Today: {new}. Keep that version.",
    "{game}: {new}. {name} beats their median of {usual}. Tidy.",
    "{name}, you were sharp on {game}: {new} (usual {usual}).",
    "{name}'s {game} today: {new}, ahead of a usual {usual}.",
    "{name}, {new} on {game} — that's your better-than-average self (usual: {usual}).",
    "Good one, {name}: {game} in {new}, versus a usual {usual}.",
    "{name}, your {game} median is {usual}. You just went {new}. Well played.",
    "{name} on {game}: {new}. Comfortably better than their {usual} norm.",
    "{name}, {game} in {new}. That beats your typical {usual} handily.",
    "{new} on {game} for {name}. Usually {usual}. Today, a step up.",
    "{name}, nice {game}: {new}. Better than your regular {usual}.",
    "{name} outdoes the usual {usual} on {game}: {new}.",
    "{name}, {new} on {game}. Normal you does {usual}. Today you does better.",
    "{game} today: {new}. {name}'s typical: {usual}. A clear upgrade.",
    "{name}, {new} on {game} — better than your median {usual}. Don't stop now.",
    "{name}, good {game}: {new} against a usual {usual}. Momentum.",
    "Quick-ish {game} from {name}: {new}. Normally {usual}.",
    "{name}, {game} in {new}. A bit better than your usual {usual}. Every bit counts.",
    "{name} gets ahead of their normal on {game}: {new} vs {usual}.",
    "{name}, {game}, {new}. Better than the usual {usual}. The trend is your friend.",
    "Above-average {game} from {name} today: {new} (avg-ish {usual}).",
    "{name}, beating your {game} norm ({usual}) with {new}. Good stuff.",
    "{name}, {new} on {game} — your usual is {usual}. Take the win.",
    "{name}, today's {game} ({new}) beats the {usual} you normally manage.",
)

# {name} {game} {new} {usual}
VS_USUAL_WORSE: Tuple[str, ...] = (
    "{name}, {new} on {game} is behind your usual {usual}. Off day.",
    "Slower than normal, {name}: {game} in {new} (usually {usual}).",
    "{name}'s {game}: {new}. Typical {name}: {usual}. Today's {name}: distracted.",
    "{new} on {game}, {name}. Your median is {usual}. Today wasn't median.",
    "{name}, you normally do {game} in about {usual}. Today: {new}. Hmm.",
    "{game} {new}, {name} — a bit off your usual {usual}.",
    "{name} misses their typical {game} ({usual}) with {new}.",
    "{name}, {new} on {game}. Behind your normal pace ({usual}).",
    "Below-usual {game} from {name}: {new} vs {usual}.",
    "{name}, {game} in {new}. Your normal is {usual}. Today is not normal.",
    "{name} is having a slow {game} day: {new} (median {usual}).",
    "{new} on {game}. {name}'s usual: {usual}. Regression noted.",
    "{name}, {game} was a slog: {new} vs your typical {usual}.",
    "Below-par {game} for {name}: {new}. Normally {usual}.",
    "{name}, {new} on {game}. Slower than your usual {usual}. Not enough coffee?",
    "{name} underperforms their usual {game} ({usual}): {new}.",
    "{name}, typical {game} for you: {usual}. Today: {new}. Who was that?",
    "{game}: {new}. {name} misses their median of {usual}. It happens.",
    "{name}, you were sluggish on {game}: {new} (usual {usual}).",
    "{name}'s {game} today: {new}, behind a usual {usual}.",
    "{name}, {new} on {game} — your worse-than-average self showed up (usual: {usual}).",
    "Meh, {name}: {game} in {new}, versus a usual {usual}.",
    "{name}, your {game} median is {usual}. You just went {new}. Oops.",
    "{name} on {game}: {new}. A step back from their {usual} norm.",
    "{name}, {game} in {new}. Your typical {usual} would have been nice.",
    "{new} on {game} for {name}. Usually {usual}. Today, a wobble.",
    "{name}, not your sharpest {game}: {new}. Normally {usual}.",
    "{name} falls short of the usual {usual} on {game}: {new}.",
    "{name}, {new} on {game}. Normal you does {usual}. Normal you was missed.",
    "{game} today: {new}. {name}'s typical: {usual}. A slight dip.",
    "{name}, {new} on {game} — slower than your median {usual}. Shake it off.",
    "{name}, slow {game}: {new} against a usual {usual}. Tomorrow.",
    "Slow-ish {game} from {name}: {new}. Normally {usual}.",
    "{name}, {game} in {new}. A bit off your usual {usual}. No drama.",
    "{name} drifts behind their normal on {game}: {new} vs {usual}.",
    "{name}, {game}, {new}. Behind the usual {usual}. Blip, surely.",
    "Below-average {game} from {name} today: {new} (avg-ish {usual}).",
    "{name}, missing your {game} norm ({usual}) with {new}. Reset tomorrow.",
    "{name}, {new} on {game} — your usual is {usual}. Today was unusual.",
    "{name}, today's {game} ({new}) lags the {usual} you normally manage.",
)

# {name} {game} {new} {usual}
VS_USUAL_PAR: Tuple[str, ...] = (
    "{name}, {new} on {game}. Right on your usual {usual}. Steady.",
    "Bang-average {game} from {name}: {new} (usually {usual}).",
    "{name}'s {game}: {new}. Typical {name}: {usual}. Today's {name}: typical.",
    "{new} on {game}, {name}. About your median ({usual}). Reliable.",
    "{name}, you normally do {game} in about {usual}. Today: {new}. Spot on.",
    "{game} {new}, {name} — dead on your usual.",
    "{name} hits their typical {game} mark: {new} (norm {usual}).",
    "{name}, {new} on {game}. Exactly the {name} we know.",
    "On-par {game} from {name}: {new} vs {usual}.",
    "{name}, {game} in {new}. Normal. Comfortingly normal.",
    "{name}'s {game} today: {new}. Median: {usual}. Clockwork.",
    "{new} on {game}. {name}'s usual: {usual}. Nothing to see here. In a good way.",
    "{name}, {game} as per usual: {new}.",
    "Par {game} for {name}: {new}. Normally {usual}. Predictable. Admirable.",
    "{name}, {new} on {game}. Your usual. The metronome ticks on.",
    "{name} lands right on their {game} norm: {new}.",
    "{name}, typical {game} for you: {usual}. Today: {new}. Textbook.",
    "{game}: {new}. {name} right on the median ({usual}).",
    "{name}, a by-the-book {game}: {new}.",
    "{name}'s {game} today: {new}, right around the usual {usual}.",
    "{name}, {new} on {game}. Neither hero nor villain today.",
    "Average {game} for {name}: {new}. Average is a compliment here.",
    "{name}, your {game} median is {usual}. You did {new}. Consistency personified.",
    "{name} on {game}: {new}. Exactly on brand.",
    "{name}, {game} in {new}. Close enough to {usual} to call it a habit.",
    "{new} on {game} for {name}. Usual service.",
    "{name}, regulation {game}: {new}.",
    "{name} keeps it steady on {game}: {new} (norm {usual}).",
    "{name}, {new} on {game}. The usual. Boring, effective.",
    "{game} today: {new}. {name}'s typical. Dependable as a Tuesday.",
)

# {p1} {p2} {gap} {game}
PHOTO_FINISH: Tuple[str, ...] = (
    "📸 Photo finish! {p1} and {p2} are {gap} points apart. {game} could decide it.",
    "📸 {p1} vs {p2}: {gap} points in it. Somebody fetch the camera. Eyes on {game}.",
    "📸 Too close to call: {p1} and {p2}, {gap} points apart. {game} is the decider.",
    "📸 {gap} points between {p1} and {p2}. Every second of {game} matters now.",
    "📸 It's tight at the top: {p1} and {p2} separated by {gap}. {game} next.",
    "📸 {p1} and {p2} — {gap} points. Hold your breath. Eyes on {game}.",
    "📸 Neck and neck: {p1}, {p2}, {gap} points. {game} could swing it.",
    "📸 {p1} leads {p2} by just {gap}. Watch this space. Eyes on {game}.",
    "📸 A {gap}-point gap between {p1} and {p2}. Nails are being bitten. Eyes on {game}.",
    "📸 {p1} and {p2} within {gap}. The {game} result could flip it.",
    "📸 Finish-line drama: {p1} vs {p2}, {gap} points. Eyes on {game}.",
    "📸 {gap} points! {p1} and {p2} are inseparable. Eyes on {game}.",
    "📸 {p1} edges {p2} by {gap}. Nothing is settled. Eyes on {game}.",
    "📸 {p1} and {p2} are split by {gap}. {game} is suddenly very important.",
    "📸 Photo finish brewing: {p1} and {p2}, {gap} apart. Eyes on {game}.",
)

# {name} {prior_leader} {days_held} {day_word}
COMEBACK: Tuple[str, ...] = (
    "⚡ {name} storms to the top. {prior_leader}'s {days_held}-{day_word} reign: over.",
    "⚡ Comeback! {name} overtakes {prior_leader}, who'd led for {days_held} {day_word}.",
    "⚡ {name} is your new leader. {prior_leader} had {days_held} {day_word} up there.",
    "⚡ {prior_leader} led for {days_held} {day_word}. Then {name} happened.",
    "⚡ From the chasing pack to first: {name}. Sorry, {prior_leader}.",
    "⚡ {name} snatches the lead off {prior_leader} ({days_held} {day_word} on top).",
    "⚡ New leader: {name}. {prior_leader}'s {days_held}-{day_word} run ends.",
    "⚡ {name} climbs to first. {prior_leader}, the crown has moved.",
    "⚡ {name} roars back to the top. {prior_leader} held it {days_held} {day_word}.",
    "⚡ Plot twist: {name} leads. {prior_leader}'s {days_held} {day_word} at the top are done.",
    "⚡ {name} takes first from {prior_leader}. A {days_held}-{day_word} lead evaporates.",
    "⚡ {name} is number one. {prior_leader} is not. Things change fast.",
)

# ---------------------------------------------------------------------------
# Scheduled DMs (app.jobs)
# ---------------------------------------------------------------------------

# {name}
MORNING_OPENERS_NO_PROGRESS: Tuple[str, ...] = (
    "Morning {name}. The puzzles are up and you're not on the board.",
    "{name}, your LinkedIn games are getting lonely.",
    "Rise and grind, {name}. Mostly grind. Puzzle grind.",
    "{name}, nothing logged yet today. The leaderboard has questions.",
    "Hello {name}. Today's tally so far: zero. Let's fix that.",
    "{name}, the day is young and so is your scorecard. It's empty.",
    "Psst, {name}. The puzzles. They're waiting.",
    "{name}, blank sheet today. Draw something on it.",
    "Good morning {name}. Coffee first, puzzles second, glory third.",
    "{name}, you've played nothing today. The others have noticed.",
    "{name}, zero games logged. Bold strategy. Let's see if it pays off.",
    "Morning {name}! Fresh puzzles, fresh chances, fresh embarrassments. Pick one.",
    "{name}, your streak called. It's worried about you.",
    "Nothing from you yet today, {name}. Everything alright?",
    "{name}, today's scoreboard is waiting for your name.",
    "Top of the morning, {name}. Bottom of the leaderboard, currently.",
    "{name}, not a single share today. The bot is concerned.",
    "Wakey wakey, {name}. Brain on. Puzzles open.",
    "{name}, the puzzles won't solve themselves. Believe us, we asked.",
    "Daily reminder, {name}: you haven't played yet.",
    "{name}, an untouched day of puzzles. A rare and beautiful thing. Ruin it.",
    "{name}, the rest of the group is warming up. You're still in bed. Metaphorically.",
    "Hey {name}. Puzzles: live. You: absent.",
    "{name}, you're currently on zero today. Zero is a lonely number.",
    "{name}, it's puzzle o'clock.",
    "Morning {name}. Your thinking cap is on the hook by the door.",
    "{name}, no scores yet. The pressure is entirely imaginary but also real.",
    "Calling {name}. Come in, {name}. The puzzles need you.",
    "{name}, today's games are open and your slot is empty.",
    "{name}, haven't seen you on the board today. Did you sleep in?",
    "Still nothing today, {name}. The suspense is killing us.",
    "{name}, the puzzles are fresh. You should be too.",
    "{name}, empty-handed so far today. Let's get some shares going.",
    "Day's ticking, {name}. Zero puzzles down.",
    "{name}, a whole day of puzzles and you haven't touched one.",
    "Good day {name}. It'll be a better one once you've played.",
    "{name}, your rivals are already stretching. You're still on the couch.",
    "Nothing in from {name} yet. Let's change that, shall we?",
    "{name}, consider this your polite tap on the shoulder.",
    "{name}, today's leaderboard has a {name}-shaped hole in it.",
)

# {name} {played}
MORNING_OPENERS_PARTIAL: Tuple[str, ...] = (
    "Nice start, {name}: {played} done. Let's finish the set.",
    "{name}, {played} in the bag. A few more to go.",
    "Halfway house, {name}? You've done {played}.",
    "{name}, {played} logged. The rest are getting jealous.",
    "Progress report, {name}: {played} complete. Keep going.",
    "{name} has cleared {played}. The others are still waiting.",
    "Good going {name} — {played} done. Don't leave the rest hanging.",
    "{name}, {played} sorted. Now for the rest.",
    "You've knocked out {played}, {name}. Finish the job.",
    "{name}: {played}, tick. Everything else, not yet.",
    "Solid start {name}, {played} in. Bring it home.",
    "{name}, {played} conquered. More hills remain.",
    "{played} — done. Nice, {name}. A few left on the list.",
    "{name}, you're partway through: {played}.",
    "Ticked off so far, {name}: {played}. Keep ticking.",
    "{name}, {played} down. Let's not leave any behind.",
    "Halfway hero {name}: {played} done.",
    "{name} has played {played}. The rest are calling.",
    "{played} are on the board, {name}. The board wants more.",
    "{name}, {played} complete. The job's half done. Or some fraction of done.",
    "Good effort {name}, {played} in. Finish strong.",
    "{name}, {played} logged. Don't stop now.",
    "You've done {played}, {name}. Momentum is a wonderful thing.",
    "{name}, {played} cleared. The rest are getting nervous.",
    "Partial credit for {name}: {played}. Full credit requires the rest.",
    "{name}, {played} on the board. Let's fill it up.",
    "{played}: done. {name}: nearly done. Nearly.",
    "{name}, you've got {played} sorted. Nice warm-up.",
    "Some done, some not, {name}. Done: {played}.",
    "{name}, you've handled {played}. The others are next.",
    "Keep it rolling, {name}. {played} already logged.",
    "{name}, {played} complete. You're on a roll. Roll on.",
    "{name}, {played} — tick, tick. More ticks needed.",
    "{played} done and dusted, {name}. Dust the rest too.",
    "{name}, you're on your way: {played} logged.",
)

# (no placeholders)
MORNING_SIGN_OFFS: Tuple[str, ...] = (
    "Send the shares over when you're done.",
    "DM me each one as you finish.",
    "Paste the shares here and I'll do the maths.",
    "I'll be here, waiting for shares.",
    "Shares to me, glory to you.",
    "Forward the results and I'll update the board.",
    "Pop the shares in here when they're done.",
    "Get playing — I'll handle the scoreboard.",
    "Shares in, points out. That's the deal.",
    "Go on then. I'll be watching the inbox.",
    "Play, share, repeat.",
    "Send them through and I'll sort the rankings.",
    "Your shares, my spreadsheet. Let's go.",
    "Drop the shares here as you go.",
    "Clock's ticking. Shares to me.",
    "One share per game, straight into this chat.",
    "I'll take it from there once you share.",
    "Finish up and send them my way.",
    "Ready when you are.",
    "No rush. (Slight rush.)",
    "Get stuck in.",
    "Make it count, then send it in.",
    "Shares here, please and thank you.",
    "Over to you.",
    "Go get 'em.",
    "Off you pop.",
    "Puzzles first, shares second, gloating third.",
    "The leaderboard awaits your input.",
    "Good luck. You won't need it. You might.",
    "Chop chop.",
    "I believe in you. Mostly.",
    "Send 'em through when they're cracked.",
    "Crack on, then share.",
    "Let's see what you've got.",
    "Your public awaits.",
    "Shares to the bot. The bot is hungry.",
    "Once you're done, paste them in. Simple.",
    "Go on, impress me.",
    "Over and out. Shares in.",
    "Puzzle well.",
)

# {name} {points}
NUDGE_CTX_LEADER: Tuple[str, ...] = (
    "{name}, you're top of the week on {points}. Keep the lead.",
    "Leader's privilege, {name}: {points} and a target on your back.",
    "{name}, you're number one with {points}. Don't get comfy.",
    "Top spot, {points}. {name}, defend it.",
    "{name} leads the week on {points}. Everyone's coming for you.",
    "{name}, {points} and in first. Today's games protect that.",
    "Week leader {name} ({points}). Miss today and that changes.",
    "{name}, you're ahead on {points}. Stay ahead.",
    "{points} has you on top, {name}. Keep feeding it.",
    "{name}, first place, {points}. The chasers are close.",
)

# {name} {period}
NUDGE_CTX_LAST_DAY: Tuple[str, ...] = (
    "{name}, final day of the {period}. Last chance for points.",
    "Last day of the {period}, {name}. Leave nothing on the table.",
    "{name}, the {period} ends today. Make it a good ending.",
    "It's {period}-end, {name}. Every game counts double. (It doesn't. It feels like it.)",
    "{name}, today wraps up the {period}. Finish strong.",
    "The {period} closes tonight, {name}. Get your games in.",
    "{name}, this is the {period}'s last puzzle day.",
    "Last call for the {period}, {name}.",
    "{name}, the {period} is almost done. Are you?",
    "{period} finale today, {name}. Don't miss it.",
)

# {name} {game} {streak} {streak_plus_one}
NUDGE_CTX_STREAK: Tuple[str, ...] = (
    "{name}, you've won {game} {streak} days running. Make it {streak_plus_one}.",
    "{streak}-day {game} winning streak, {name}. Don't let it end today.",
    "{name}, {game} champ for {streak} straight days. Going for {streak_plus_one}?",
    "{name} owns {game}: {streak} wins in a row. Defend it.",
    "{streak} on the bounce on {game}, {name}. The group wants to break it.",
    "{name}, {streak} straight {game} wins. {streak_plus_one} has a nice ring to it.",
    "{game} streak alert: {name}, {streak} days. Keep it alive.",
    "{name}, your {game} run is at {streak}. Don't drop it now.",
    "{name}, unbeaten on {game} for {streak} days. Today's the test.",
    "{streak} days as {game} winner, {name}. The others are plotting.",
)

# {name} {missing}
PRE_RESET_2H: Tuple[str, ...] = (
    "{name}, two hours until the puzzles reset. Still to do: {missing}.",
    "Heads up {name}: 2h left. {missing} still open.",
    "{name}, you've got two hours for {missing}. Plenty. Probably.",
    "Two-hour warning, {name}. {missing} awaits.",
    "{name}, {missing} still unplayed, 2 hours to go.",
    "Gentle reminder {name}: {missing} closes in 2 hours.",
    "{name}, 120 minutes on the clock for {missing}.",
    "Two hours left today, {name}. Outstanding: {missing}.",
    "{name}, there's time for {missing}. About two hours of it.",
    "Friendly ping, {name}: {missing} still pending, 2h to reset.",
    "{name}, the day's winding down. {missing} isn't done.",
    "Puzzle curfew in 2 hours, {name}. {missing} still out.",
    "{name}, 2h warning. {missing} needs you.",
    "Evening, {name}. {missing} still to play before the reset.",
    "{name}, two hours to squeeze in {missing}.",
)

# {name} {missing}
PRE_RESET_1H: Tuple[str, ...] = (
    "{name}, one hour left. {missing} still undone.",
    "60 minutes, {name}. {missing}. Go.",
    "{name}, the hour has come. Literally. {missing} closes in 60.",
    "One hour warning: {missing}, {name}.",
    "{name}, {missing} expires in an hour.",
    "Tick tock {name}. An hour left for {missing}.",
    "{name}, 1h to reset. {missing} still on your plate.",
    "Last hour, {name}. {missing} is waiting.",
    "{name}, {missing} — one hour. Don't leave it.",
    "An hour to go, {name}. Outstanding: {missing}.",
    "{name}, sixty minutes. {missing}. That's the whole message.",
    "{name}, the clock says one hour. {missing} says hurry.",
    "Final hour for {missing}, {name}.",
    "{name}, {missing} is still open. Not for long: one hour.",
    "One hour, {name}. Put the kettle on and play {missing}.",
)

# {name} {missing}
PRE_RESET_30MIN: Tuple[str, ...] = (
    "{name}! 30 minutes. {missing}.",
    "Half an hour, {name}. {missing} still unplayed.",
    "{name}, 30 mins to reset. {missing}. Now's good.",
    "Thirty minutes left, {name}. {missing}. Chop chop.",
    "{name}, {missing} expires in half an hour.",
    "30-minute warning, {name}: {missing}.",
    "{name}, the clock is loud now. 30 mins. {missing}.",
    "Half-hour klaxon for {name}: {missing}.",
    "{name}, {missing} — 30 minutes. Stop scrolling.",
    "{name}, you have 30 minutes and {missing}. Do the maths.",
    "Thirty, {name}. Thirty minutes. {missing}.",
    "{name}, half an hour. {missing}. Your streak is watching.",
    "Urgent-ish: {name}, 30 mins for {missing}.",
    "{name}, {missing} is about to turn into a pumpkin. 30 minutes.",
    "30 minutes, {name}. {missing}. The group would hate to see you miss it. (They'd love it.)",
)

# {name} {missing}
PRE_RESET_5MIN: Tuple[str, ...] = (
    "{name}!!! FIVE MINUTES. {missing}!!!",
    "5 MIN {name}. {missing}. GO GO GO.",
    "{name} — {missing} — 5 MINUTES — MOVE.",
    "LAST CALL {name}. {missing}. FIVE MINUTES.",
    "{name}, 5 minutes. {missing}. Why are you still reading?",
    "FIVE. MINUTES. {name}. {missing}.",
    "{name}!! {missing}!! 5 mins!!",
    "Emergency broadcast for {name}: {missing}, 5 minutes.",
    "{name}, it's now or never. {missing}. 5 minutes.",
    "TICK TOCK {name}. {missing}. FIVE MINUTES LEFT.",
    "{name}, 300 seconds. {missing}. Sprint.",
    "5 MINUTES {name}!! {missing}!! THE STREAK!!",
    "{name}, drop everything. {missing}. 5 minutes.",
    "Final countdown, {name}: {missing}. Five minutes.",
    "{name}. {missing}. Five. Minutes. GO.",
)

# (no placeholders)
NEW_GAMES: Tuple[str, ...] = (
    "New puzzles are live. Leaderboard reset. Go. Get in it.",
    "It's a brand new puzzle day. Everybody back to zero. Get in it.",
    "Fresh grids, fresh glory. Today's games are up. Get in it.",
    "Today's LinkedIn puzzles have dropped. First one in sets the pace. Get in it.",
    "New day, new games, same old rivalries. Get in it.",
    "The puzzles reset. Your excuses didn't. Go play. Get in it.",
    "Ding! Today's puzzles are ready. Fill the board. Get in it.",
    "Clean slate. Fresh puzzles. Who's first? Get in it.",
    "Today's games are up. Yesterday's losers get a second chance. Get in it.",
    "New puzzles. Same group chat. Fight. Get in it.",
    "Puzzles out. Brains on. Shares in. Get in it.",
    "Today's games are live. The early bird gets the best-of-day. Get in it.",
    "Midnight in LA means puzzle time for us. They're up. Get in it.",
    "Reset complete. New games await. Get in it.",
    "New puzzles just landed. Who's feeling sharp? Get in it.",
    "Today's board is empty. Somebody put something on it. Get in it.",
    "The daily drop is here. You know what to do. Get in it.",
    "Puzzles refreshed. Rankings reset. Egos on the line. Get in it.",
    "Here we go again. New puzzles, live now. Get in it.",
    "New games are up. Last place yesterday? Doesn't matter today. Get in it.",
    "Puzzle day has begun. Make it a good one. Get in it.",
    "Fresh puzzles, warm-ish takes. Get playing. Get in it.",
    "Brand new games are out. The scoreboard is hungry. Get in it.",
    "Today's LinkedIn games are now open. Doors are open, come on in. Get in it.",
    "New puzzles live. Somebody's about to have a great day. Somebody isn't. Get in it.",
    "The grids are fresh. The leaderboard is bare. Go. Get in it.",
    "Puzzles reset. Coffee optional. Shares mandatory. Get in it.",
    "A new day of puzzles begins. May the fastest win. Get in it.",
    "Today's games have arrived. Let's see who's awake. Get in it.",
    "Rollover complete. New games live. Get in it.",
    "The daily puzzles are out. First share takes the early lead. Get in it.",
    "New puzzles, new chances. Who's starting? Get in it.",
    "Puzzles are back. So is the pressure. Get in it.",
    "Today's games: live. Your scores: pending. Get in it.",
    "Fresh puzzles have dropped. Show us something. Get in it.",
    "It's a new puzzle day. Let the trash talk begin. Get in it.",
    "Reset done. Board clear. Play. Get in it.",
    "New games out. The leaderboard has never been more empty. Get in it.",
    "Today's puzzles are ready when you are. Ideally, now. Get in it.",
    "Another day, another set of puzzles. Let's go. Get in it.",
)

# {sender}
BRAG: Tuple[str, ...] = (
    "{sender} would like everyone to know they're having a great puzzle day.",
    "Brag alert: {sender} is on fire. Grab an extinguisher.",
    "{sender} says: catch me if you can.",
    "{sender} has pressed the brag button. Translation: they're good today.",
    "{sender} is feeling smug and wants witnesses.",
    "Notice from {sender}: today's puzzles have been dealt with.",
    "{sender} is in peak form. Respond accordingly.",
    "{sender} has asked me to inform you that they are, in fact, the best.",
    "Breaking: {sender} is having a day. A good one.",
    "{sender} brags. The group sighs. The cycle continues.",
    "{sender} wants you to know they're crushing it. You've been told.",
    "Hear ye, hear ye: {sender} is in the zone.",
    "{sender} is basking. Do not disturb the basking.",
    "{sender} would like a round of applause. Optional. Expected.",
    "{sender} is out here winning and wants the receipts acknowledged.",
    "Official bulletin: {sender} is better than you today.",
    "{sender} has spoken: today is their day.",
    "{sender} is strutting. Metaphorically. Possibly literally.",
    "{sender} brags with confidence. Prove them wrong.",
    "{sender} has thrown down the gauntlet. Pick it up.",
)

# {sender}
GRIPE: Tuple[str, ...] = (
    "{sender} is having a shocker and needs the group's sympathy.",
    "Gripe alert: {sender} would like to complain about today's puzzles.",
    "{sender} says the puzzles are rigged. The puzzles deny it.",
    "{sender} has pressed the gripe button. Thoughts and prayers.",
    "{sender} is not having a good one. Be nice. Or don't.",
    "Notice from {sender}: today's puzzles won.",
    "{sender} is in a slump and wants company.",
    "{sender} would like to formally lodge a complaint with LinkedIn.",
    "Breaking: {sender} is having a day. A bad one.",
    "{sender} gripes. The group smirks. The cycle continues.",
    "{sender} wants you to know it's not their fault. It's probably their fault.",
    "Hear ye, hear ye: {sender} has been defeated by a grid.",
    "{sender} is sulking. Do not disturb the sulking.",
    "{sender} would like a hug. Or a worse score from someone else.",
    "{sender} has been humbled and wants witnesses.",
    "Official bulletin: {sender} had a stinker.",
    "{sender} has spoken: today is not their day.",
    "{sender} is shaking a fist at the sky. And at the grid.",
    "{sender} requests that everyone else also play badly, in solidarity.",
    "{sender} has thrown in the towel for today. Pick it up and wipe your tears.",
)

# ---------------------------------------------------------------------------
# Recap "who's missing" lines (app.scheduler)
# ---------------------------------------------------------------------------

# {names}
MISSING_TODAY_SINGULAR: Tuple[str, ...] = (
    "Missing in action: {names}.",
    "{names} didn't play. The board noticed.",
    "No shares from {names}. Busy day, or hiding?",
    "{names} skipped it. Zero points, maximum mystery.",
    "One empty seat at the table: {names}.",
    "{names} was nowhere to be seen.",
    "Absent without leave: {names}.",
    "{names}, the puzzles missed you.",
    "{names} took the day off. Brave.",
    "Not a peep from {names}.",
    "{names} is officially unaccounted for.",
    "The group was one short: {names}.",
    "{names}: no scores, no excuses filed.",
    "Search party required for {names}.",
    "{names} ghosted the puzzles.",
    "Didn't play: {names}. We'll be talking about this.",
)

# {names}
MISSING_TODAY_PLURAL: Tuple[str, ...] = (
    "Missing in action: {names}.",
    "{names} didn't play. The board noticed.",
    "No shares from {names}. Busy day, or hiding?",
    "{names} all skipped it. Zero points between them.",
    "Empty seats at the table: {names}.",
    "{names} were nowhere to be seen.",
    "Absent without leave: {names}.",
    "{names}, the puzzles missed you.",
    "{names} took the day off. Coordinated? Suspicious.",
    "Not a peep from {names}.",
    "{names} are officially unaccounted for.",
    "The group was short-handed: {names}.",
    "{names}: no scores, no excuses filed.",
    "Search party required for {names}.",
    "{names} ghosted the puzzles.",
    "Didn't play: {names}. We'll be talking about this.",
)


# ---------------------------------------------------------------------------
# Tone layers
# ---------------------------------------------------------------------------
# The pools above lean cheeky. These add the other registers so a
# message can land as mean, comforting or wildly supportive:
#   bad results  → + comforting, + super supportive
#   good results → + super supportive, + mean / backhanded
# Each is folded into its pool at the bottom of the file.

_NEW_PB_SUPPORTIVE = (
    "{name}!!! New {game} PB: {new}! From {prior} to {new} — we are so proud of you.",
    "{name}, {new} on {game} is your best ever (was {prior}). All that practice is paying off. Brilliant.",
    "Look at you, {name}! {game} PB of {new}, beating {prior}. You deserve every bit of this.",
    "{name}, you just bettered your {game} best ({prior}) with {new}. Genuinely brilliant work.",
    "This is what improvement looks like: {name}, {game}, {prior} → {new}. So well done.",
    "{name}, new {game} PB ({new}, was {prior})! Take a moment and enjoy it — you earned it.",
    "So happy for you, {name}. {game} in {new} is a personal best. {prior} was hard to beat and you did it.",
    "{name}, {new} on {game}! A new PB! The whole group should be cheering. (Old best: {prior}.)",
    "Proud moment, {name}: {game} PB of {new}, past your {prior}. Keep believing in yourself.",
    "{name}, you've never done {game} better than {new}. {prior} is behind you now. Amazing.",
)
_NEW_PB_MEAN = (
    "{name}, new {game} PB: {new}. Took you long enough to beat {prior}.",
    "A {game} PB for {name}: {new}. Still not top of the group, but sure, celebrate. (Was {prior}.)",
    "{name} finally beats {prior} on {game} with {new}. The rest of us did that months ago.",
    "{new} on {game}, a PB for {name}. Low bar, cleared. Old bar: {prior}.",
    "{name}, {game} PB of {new}. Don't let it go to your head. It will. Old best {prior}.",
    "Congrats on the {game} PB, {name} ({new}, was {prior}). Shame it'll never happen again.",
    "{name} PBs on {game}: {new}. Was the puzzle easy today? (Asking because {prior}.)",
    "{name}, {new} on {game} is your best. Your best. Not the best. Previous: {prior}.",
    "Oh look, {name} got a {game} PB: {new}. Even a stopped clock, etc. Old one: {prior}.",
    "{name}, beating {prior} with {new} on {game}. Enjoy it, it's all downhill from here.",
)
_TIED_PB_SUPPORTIVE = (
    "{name}, {new} on {game} matches your best ever! That's elite consistency. So proud.",
    "Equalling your {game} PB ({new}) is a big deal, {name}. The breakthrough is coming.",
    "{name}, you matched your best {game} at {new}. You're right at the top of your game.",
    "Another {new} on {game}, {name}! Doing your best twice is harder than doing it once. Well done.",
    "{name}, {game} PB equalled at {new}. Keep going — you've got even more in you.",
)
_TIED_PB_MEAN = (
    "{name}, {new} on {game}. Same as your PB. So, no improvement then.",
    "Tied PB on {game}, {name}: {new}. Couldn't find one extra second, eh?",
    "{name}, {new} on {game} equals your best. Plateau achieved.",
    "{game} {new}, {name}. Exactly your PB. Exactly as good as you've ever been. No better.",
    "{name} ties their {game} best at {new}. So close. So, so close. Not close enough.",
)
_NEW_WORST_COMFORT = (
    "{name}, {new} on {game} is your slowest yet (was {prior}). Tough one — everyone has them. Be kind to yourself.",
    "Rough {game} today, {name}: {new}. Your old worst was {prior}. One puzzle doesn't define you.",
    "{name}, {game} took {new} today (previous worst {prior}). It happens to the best. Tomorrow's fresh.",
    "Hey {name} — {new} on {game} is a personal worst, but you still finished it. That counts. (Was {prior}.)",
    "{name}, sometimes the puzzle just wins. {game}: {new} (old worst {prior}). You'll bounce back.",
    "{name}, {new} on {game} isn't the number you wanted (worst was {prior}). Shake it off — you're still good at this.",
    "Not your day on {game}, {name} ({new}, was {prior}). Grab a cuppa. It's only a puzzle.",
    "{name}, a new {game} low of {new} (from {prior}). Bad days make the good ones sweeter.",
    "{name}, {game} in {new} — your slowest. Previous: {prior}. Brains have off days too. Rest up.",
    "{name}, {new} on {game} is a personal worst ({prior} before). Don't stress it — you showed up and did it.",
)
_NEW_WORST_SUPPORTIVE = (
    "{name}, {new} on {game} (worst was {prior}) — and you STILL finished. That's grit. Proud of you.",
    "{name}, personal worst on {game} ({new}, was {prior}), but you never gave up on it. Legend behaviour.",
    "{name}, a slow {game} ({new}; old worst {prior}) just means a comeback is coming. We believe in you!",
    "{new} on {game}, {name}. Your worst yet (was {prior}), but persistence is the real skill here. Well done for sticking with it.",
    "{name}, sticking with a {game} that long ({new}, was {prior}) takes determination. Tomorrow you'll fly.",
    "{name}! Worst {game} yet ({new}, was {prior}) but you're still in the game, and that's what matters. Go you.",
    "Every great player has a {new} on {game} in them, {name}. (Old worst: {prior}.) Onwards and upwards!",
    "{name}, {game} in {new} — tough, but the fact you finished it is a win. Old worst {prior}. Keep going!",
    "{name}, {new} on {game} (prev worst {prior}). You'll look back on this from the top of the board. Chin up!",
    "{name}, worst {game} ({new}, was {prior})? Doesn't matter. You're still one of the best in this group.",
)
_TIED_WORST_COMFORT = (
    "{name}, {new} on {game} ties your slowest, but it didn't get worse. Small wins.",
    "Tough {game} again, {name} ({new}). It's okay — tomorrow's a new grid.",
    "{name}, equal-worst {game} at {new}. Happens. Breathe. Move on.",
    "{name}, {new} on {game} — not your best day, but you got through it.",
    "{name}, {game} in {new}, matching your worst. Don't be hard on yourself.",
)
_TIED_WORST_SUPPORTIVE = (
    "{name}, {new} on {game} — equal worst, but you finished it! That's what counts. Proud of you.",
    "{name}, tied worst on {game} ({new}), and you still came back to play. Absolute commitment!",
    "{name}, {game} at {new} today. You've bounced back from this before and you will again!",
    "{name}! {new} on {game} isn't the end of anything. Your best days are ahead. Go get them!",
    "{name}, matching your slowest {game} ({new}) just sets up the comeback story. We're rooting for you.",
)
_WORST_OF_DAY_COMFORT = (
    "{name}, {new} puts you last on {game} today, but it's one puzzle. You're doing fine.",
    "Slowest {game} today, {name} ({new}). Someone always has to be — no shame in it.",
    "{name}, last on {game} ({new}), but there's a whole week of points still to play for.",
    "{name}, {game} in {new} — bottom today, but you played, which is more than nothing.",
    "Not your {game} today, {name} ({new}). Tomorrow's leaderboard is a clean slate.",
    "{name}, {new} on {game} is last for now. Plenty of people still to play — and it's just a game.",
    "{name}, bringing up the rear on {game} ({new}). Be gentle on yourself. It's meant to be fun.",
    "{name}, {game} today: {new}, last place. One day doesn't make a week.",
    "{name}, {new} on {game}. Slowest today, but you're still very much in this.",
    "{name}, last on today's {game} ({new}). Have a biscuit. You deserve one anyway.",
)
_WORST_OF_DAY_SUPPORTIVE = (
    "{name}, {new} on {game} — last today, but you showed up and played! That matters. 💪",
    "{name}! Bottom of {game} today ({new}) but top of our hearts. Keep going!",
    "{name}, {game} in {new}. Last place is just the start of the comeback arc. We believe!",
    "{name}, slowest {game} today ({new}), but you're consistent, you're here, and you're improving. Proud of you.",
    "{name}, {new} on {game}! Every puzzle you play makes you better. Tomorrow's yours.",
    "{name}, last on {game} ({new}) — and still one of the most dedicated players we've got. Respect.",
    "{name}, {game} in {new}. Not the top today, but you never quit. That's a champion's habit.",
    "{name}, {new} on {game} — the leaderboard doesn't show heart, but we see it. Go you!",
    "{name}! Last on {game} today ({new}) but you'll be flying up the board soon. We know it.",
    "{name}, {game} {new}. Every legend has a last-place day. This is yours. The rise starts now!",
)
_ANTI_RECORD_COMFORT = (
    "{name}, {new} on {game} is the group's slowest ever, but finishing it at all was the point.",
    "{name}, {game} in {new} — a record nobody wants, but a story everyone will laugh about. It's okay.",
    "{name}, group's slowest {game} ({new}). Honestly, the puzzle was probably brutal today.",
    "{name}, {new} on {game} — it's one bad day in a long run of good ones.",
    "{name}, slowest {game} in group history ({new}). Shrug it off. It's a game, not a verdict.",
)
_ANTI_RECORD_SUPPORTIVE = (
    "{name}, {new} on {game} — group's slowest ever — and you STILL saw it through. Hero.",
    "{name}! Longest {game} on record ({new}), but you didn't give up. That's real determination.",
    "{name}, {game} in {new}. You've made history and you've got nowhere to go but up!",
    "{name}, slowest {game} ever ({new}), but that's the most stubborn finish we've seen. Love it.",
    "{name}, {new} on {game}. Anyone can quit. You didn't. That's worth celebrating.",
)
_DOW_WORST_COMFORT = (
    "{name}, {weekday} {game} was tough ({new}; old {weekday} worst {prior}). {weekday}s are like that.",
    "{name}, slowest {weekday} {game} ({new}, was {prior}). Next {weekday} will be kinder.",
    "{name}, {game} on a {weekday} in {new} (prior {weekday} worst {prior}). Rest up, it's fine.",
    "Not the best {weekday}, {name}: {game} {new} (was {prior}). One day of the week doesn't matter much.",
    "{name}, {new} on {weekday} {game} (old low {prior}). Everyone has a day that doesn't suit them.",
)
_DOW_WORST_SUPPORTIVE = (
    "{name}, {weekday} {game} in {new} (was {prior}) — you'll own {weekday}s soon. We believe in you!",
    "{name}! Slow {weekday} {game} ({new}, old {prior}), but you showed up. That's the habit that wins.",
    "{name}, {new} on {weekday} {game} (worst was {prior}). Next {weekday}: comeback time!",
    "{name}, toughest {weekday} {game} yet ({new}, was {prior}) and you finished it. Proud of you.",
    "{name}, {weekday} {game} {new} (prev {prior}). You've got this — {weekday}s don't stand a chance.",
)
_BOTTOM_Q_COMFORT = (
    "{name}, {new} on {game} is on the slower side for you, but it's just one game.",
    "{name}, {game} in {new} — not your quickest, but nothing to worry about.",
    "{name}, an off day on {game} ({new}). Everyone has them. Tomorrow's a reset.",
    "{name}, {new} on {game}. Not your best, but you'll be back to form soon.",
    "{name}, {game} took a little longer today ({new}). Totally normal.",
    "{name}, slower {game} today ({new}). Don't sweat it — the average evens out.",
    "{name}, {new} on {game}. Some days the brain's just warming up.",
    "{name}, {game} in {new} — you've done better and you'll do better again.",
)
_BOTTOM_Q_SUPPORTIVE = (
    "{name}, {new} on {game} — slower than usual, but you played! Consistency wins in the end.",
    "{name}! {game} in {new}. Off days build champions. You'll smash it tomorrow.",
    "{name}, {new} on {game}. You're better than this score and we all know it. Go again tomorrow!",
    "{name}, slower {game} ({new}), but you keep turning up. That's the real skill. Proud of you.",
    "{name}, {game} {new}. Not your best, but your best is brilliant, and it's coming back.",
    "{name}! {new} on {game}. Every slow day teaches you something. Keep at it!",
    "{name}, {game} in {new} — chin up! You're still a force on this leaderboard.",
    "{name}, {new} on {game}. Bad game, great player. Back at it tomorrow!",
)
_DOW_BOTTOM_Q_COMFORT = (
    "{name}, {weekday} {game} in {new} — {weekday}s are just tricky. No stress.",
    "{name}, {new} on {game}. Slow for a {weekday}, but it's only one day.",
    "{name}, {weekday}s and {game} ({new}) — not always friends. That's fine.",
    "{name}, {game} on a {weekday}: {new}. Rest up for next {weekday}.",
)
_DOW_BOTTOM_Q_SUPPORTIVE = (
    "{name}, {weekday} {game} at {new} — you'll crack {weekday}s yet! We believe in you.",
    "{name}! {new} on {game} this {weekday}. Next {weekday} is your comeback.",
    "{name}, {weekday} {game}: {new}. You keep showing up on {weekday}s. That's how you win them.",
    "{name}, {new} on a {weekday} {game}. Tough day, great effort. Go you!",
)
_ABOVE_FLOOR_COMFORT = (
    "{name}, {new} on {game} wasn't your best, but you're not last ({prior_holder}: {prior}). You're doing fine.",
    "{name}, {game} in {new} — a bit slow, but {prior_holder} found it hard too ({prior}). Tricky puzzle today.",
    "{name}, {new} on {game}. If {prior_holder} took {prior}, the puzzle was clearly a tough one. Go easy on yourself.",
    "{name}, not your quickest {game} ({new}), but plenty of people struggled today. {prior_holder}: {prior}.",
    "{name}, {new} on {game}. {prior_holder}'s {prior} says it all — hard day for everyone.",
    "{name}, {game} {new}. Tough one. {prior_holder} ({prior}) agrees. Tomorrow will be better.",
)
_ABOVE_FLOOR_SUPPORTIVE = (
    "{name}, {new} on {game} and still ahead of {prior_holder} ({prior})! Keep climbing!",
    "{name}! {game} in {new}. Not your best, but you're above {prior_holder}'s {prior}. Onwards!",
    "{name}, {new} on {game} — slow-ish, but you beat {prior_holder} ({prior}). Every place counts!",
    "{name}, {game} {new}. You're out of the basement ({prior_holder}: {prior}). Up you go!",
    "{name}, {new} on {game}. Tough puzzle, and you still beat {prior_holder} ({prior}). Proud of you.",
    "{name}! {game} in {new}, ahead of {prior_holder}'s {prior}. The climb starts here.",
)
_VS_USUAL_WORSE_COMFORT = (
    "{name}, {new} on {game} — a bit off your usual {usual}, but it's no big deal.",
    "{name}, {game} in {new} today (usually {usual}). Off days are part of it.",
    "{name}, slightly slower {game} than your {usual} norm ({new}). Totally fine.",
    "{name}, {new} on {game} vs a usual {usual}. Tomorrow you'll be back on it.",
    "{name}, {game} today: {new}. Normally {usual}. Everyone has these days — relax.",
    "{name}, {new} on {game} (usual {usual}). Not to worry, one game.",
    "{name}, a little behind your usual {usual} on {game} ({new}). Brain's allowed a rest.",
    "{name}, {game} {new} today, {usual} on a normal day. Nothing to fret about.",
)
_VS_USUAL_WORSE_SUPPORTIVE = (
    "{name}, {new} on {game} — not your usual {usual}, but you played! Back to it tomorrow!",
    "{name}! {game} in {new} (normally {usual}). You'll be back to your best in no time.",
    "{name}, {new} on {game}. Your usual is {usual}, and that's who you really are. Go you!",
    "{name}, off-day {game} ({new} vs {usual}), but your consistency is amazing. Keep it up!",
    "{name}, {game} {new} (usual {usual}). Tiny blip, big player. We believe in you!",
    "{name}! {new} on {game} today. Your normal {usual} is coming right back. Proud of you for playing.",
    "{name}, {new} on {game}, a touch off {usual}. Champions have off days too!",
    "{name}, {game} in {new} vs a {usual} usual. Tomorrow, you're back. Can't wait to see it!",
)
_GRIPE_COMFORT = (
    "{sender} is having a rough puzzle day. Send them some love.",
    "{sender} had a tough one today. Be nice — it could've been you. It will be you, some day.",
    "Group hug for {sender}, who's had a puzzle day to forget.",
    "{sender} is struggling today. A kind word wouldn't go amiss.",
    "{sender} had a stinker. It's okay, {sender}. It's just a puzzle.",
    "{sender} is feeling defeated. Remind them they're brilliant, please.",
    "{sender} had a bad day on the grids. We've all been there.",
    "Be gentle with {sender} today. The puzzles were not.",
)
_GRIPE_SUPPORTIVE = (
    "{sender} had a rough day — but {sender} always bounces back. Tomorrow's yours! 💪",
    "{sender} is down but never out. Cheer them on, everyone!",
    "{sender} had a bad one, and still showed up. That's commitment. Proud of you, {sender}.",
    "Everyone send {sender} good vibes. Comeback incoming!",
    "{sender} struggled today, but they're one of the best players here. Don't forget it.",
    "{sender} had a tough day. Next round: {sender} redemption arc. We believe!",
    "{sender} is having a moment. Let's lift them up — they'd do it for you.",
    "One bad day for {sender}, a lifetime of good ones ahead. Go {sender}!",
)
_MORNING_NO_PROGRESS_SUPPORTIVE = (
    "Morning {name}! You're going to smash today's puzzles. Can't wait to see your scores.",
    "{name}, today's the day. Fresh puzzles, and you're ready for them. Go get them!",
    "Good morning {name}! No pressure — just play, enjoy it, and send them through.",
    "{name}, we believe in you. Today's puzzles don't stand a chance.",
    "Hey {name}, whenever you're ready, the puzzles are there. You've got this!",
    "{name}! A whole fresh day of puzzles to enjoy. Have fun with them.",
    "Morning {name}. You're a great player — today's a chance to show it.",
    "{name}, take your time, enjoy your coffee, and crush those puzzles when you're ready.",
    "Rise and shine {name}! Your best scores might be today. Only one way to find out.",
    "{name}, cheering for you today. Go play!",
)
_MORNING_PARTIAL_SUPPORTIVE = (
    "Love it, {name} — {played} done already! You're on fire.",
    "{name}, great start with {played}! Keep that momentum going.",
    "So proud of you {name}: {played} in. You've totally got the rest.",
    "{name}, {played} done! You're smashing it today.",
    "Brilliant start, {name} ({played}). The rest will be a breeze.",
    "{name}, {played} already! Look at you go.",
    "{name}, great work on {played}. Finish strong — we're cheering!",
    "{played} done, {name}! Amazing. Keep going, superstar.",
)
_PRE_RESET_2H_SUPPORTIVE = (
    "{name}, two hours left and you've got loads of time for {missing}. You've got this!",
    "No stress {name} — {missing} still to go and two hours to do it. Enjoy them!",
    "{name}, still time for {missing}. Two hours. Go show them how it's done!",
    "Just a friendly nudge {name}: {missing} is waiting, 2h left. Cheering for you!",
)
_PRE_RESET_1H_SUPPORTIVE = (
    "{name}, one hour left for {missing}. Plenty of time — go smash it!",
    "{name}, you can do {missing} in an hour easily. Go on, we believe in you!",
    "One hour, {name}. {missing}. You've totally got this.",
    "{name}, an hour to go and {missing} left. Finish strong!",
)
_PRE_RESET_30MIN_SUPPORTIVE = (
    "{name}, 30 minutes for {missing}. You're fast — you'll make it!",
    "Half an hour, {name}! {missing} is very doable. Go go go!",
    "{name}, still time! {missing}, 30 minutes. Rooting for you!",
    "{name}, 30 mins and {missing} to go. Let's finish the day strong!",
)
_PRE_RESET_5MIN_SUPPORTIVE = (
    "{name}, 5 minutes! {missing}! You can do it — GO!",
    "{name}!! 5 mins for {missing}. Believe! RUN!",
    "Five minutes, {name}. {missing}. You've been faster than this before — GO!",
    "{name}, 5 minutes, {missing}. Clutch time. We believe in you!",
)
_MISSING_SINGULAR_COMFORT = (
    "{names} didn't play — hope all's well. We missed you.",
    "No scores from {names}. Hope you're having a good day, wherever you are.",
    "{names} sat this one out. Totally fine — come back when you can!",
    "Missed {names} on the board. See you tomorrow, hopefully!",
    "{names} wasn't with us today. Life happens. The puzzles will wait.",
    "Thinking of {names}, who didn't play. Hope you're okay!",
)
_MISSING_PLURAL_COMFORT = (
    "{names} didn't play — hope all's well. We missed you.",
    "No scores from {names}. Hope you're all having a good day.",
    "{names} sat this one out. Totally fine — come back when you can!",
    "Missed {names} on the board. See you tomorrow, hopefully!",
    "{names} weren't with us today. Life happens. The puzzles will wait.",
    "Thinking of {names}, who didn't play. Hope everyone's okay!",
)
_BEST_OF_DAY_SUPPORTIVE = (
    "{name}, {new} on {game} — best in the group today! Absolutely brilliant. 🌟",
    "{name}!!! Top of {game} today with {new}! So, so well done.",
    "{name}, you're leading {game} with {new}. You make this look easy. Amazing work.",
    "Best {game} today: {name}, {new}. We're all in awe. Genuinely.",
    "{name}, {new} on {game} is the one to beat. Superstar performance!",
    "{name}! {game} in {new}, top of the day. You absolute star.",
    "So proud of you {name} — {new} on {game} leads the group!",
    "{name}, fastest {game} today ({new}). Incredible. Keep shining!",
    "{name}, {game} leader with {new}! You deserve this so much.",
    "{name}, best {game} of the day: {new}! What a player. What a person.",
)
_BEST_OF_DAY_MEAN = (
    "{name} leads {game} today with {new}. Enjoy it. It won't last.",
    "{name}, best {game} today at {new}. Nobody good has played yet.",
    "Top of {game} today: {name}, {new}. A weak field, to be fair.",
    "{name}, leading {game} with {new}. Don't get used to it.",
    "{name} has the best {game} today ({new}). Must've been an easy one.",
    "{new} on {game}, best today. {name}, savour it — this is your peak.",
    "{name} on top of {game} ({new}). The others are clearly having an off day.",
    "{name}, best {game} so far ({new}). 'So far' doing a lot of work there.",
    "{name} leads {game} ({new}). The group's standards have slipped.",
    "{name}, {new} on {game} is top. For the next five minutes, probably.",
)
_ALL_TIME_RECORD_SUPPORTIVE = (
    "🏆 {name}!!! Fastest {game} this group has EVER seen: {new}. Absolutely phenomenal.",
    "🏆 {name}, {new} on {game} is the group's all-time best. You're a legend. Genuinely.",
    "🏆 Group record for {name} on {game}: {new}! Take it all in — you earned it.",
    "🏆 {name}, nobody here has ever done {game} faster than {new}. We're so proud.",
    "🏆 {name}! All-time {game} record, {new}. History made by the best of us.",
)
_ALL_TIME_RECORD_MEAN = (
    "🏆 {name} sets the {game} group record ({new}). Probably a fluke. {prior_holder} thinks so.",
    "🏆 {name}, {new} on {game}, the all-time best. Did you have help? Be honest.",
    "🏆 {game} record: {name}, {new}. Enjoy it before {prior_holder} takes it back.",
    "🏆 {name} beats {prior_holder}'s {game} record with {new}. The puzzle must've been tiny.",
    "🏆 {name} holds the {game} record now ({new}). Insufferable from here on, presumably.",
)
_DOW_PB_SUPPORTIVE = (
    "{name}, best {weekday} {game} ever: {new} (was {prior})! You're getting better every week!",
    "{name}! {weekday} {game} PB at {new}, beating {prior}. Love to see it.",
    "So proud, {name}: {weekday} {game} in {new}, a new best (old {prior}).",
    "{name}, {new} on {weekday} {game} — your best yet (was {prior}). Brilliant!",
    "{name}, you've never had a better {weekday} on {game}: {new}, from {prior}. Amazing.",
)
_DOW_PB_MEAN = (
    "{name}, best {weekday} {game}: {new} (was {prior}). Shame it's only a {weekday} record.",
    "{weekday} {game} PB for {name}: {new}. A very niche achievement. Old: {prior}.",
    "{name}, {new} on {weekday} {game} beats {prior}. Nobody's counting {weekday}s except you.",
    "{name}, {weekday} {game} best of {new} (was {prior}). Your other days are still mediocre.",
    "{name}, {weekday} PB on {game}: {new}, from {prior}. Pick a better day to peak next time.",
)
_YEAR_PB_SUPPORTIVE = (
    "{name}, best {game} of {year}: {new} (was {prior})! What a year you're having!",
    "{name}! {year} best on {game} — {new}, beating {prior}. Fantastic.",
    "So happy for you, {name}: {game} in {new}, your best of {year} (old {prior}).",
    "{name}, {new} on {game} tops your {year} (was {prior}). Keep this going!",
    "{name}, {year} {game} PB of {new}, past {prior}. You're flying this year!",
)
_YEAR_PB_MEAN = (
    "{name}, best {game} of {year}: {new} (was {prior}). Took most of the year to get there.",
    "{year} best on {game} for {name}: {new}. Not a lifetime best, though. Old: {prior}.",
    "{name}, {new} on {game}, best of {year} (was {prior}). Not saying much about {year}.",
    "{name}, {game} {year} PB: {new}, past {prior}. Last year's you would still win.",
    "{name}, {year}'s best {game}: {new} (prior {prior}). Low expectations met.",
)
_TOP_Q_SUPPORTIVE = (
    "{name}, {new} on {game} is one of your best! So good to see.",
    "{name}! {game} in {new} — top form. Love it.",
    "{name}, {new} on {game}. You're absolutely flying. Proud of you!",
    "{name}, top-quarter {game} ({new})! The hard work shows.",
    "{name}, {game} {new}. One of your finest. Keep it up, star!",
    "{name}, {new} on {game} — brilliant. You make it look easy.",
    "{name}! {new} on {game}. You're in amazing form right now.",
    "{name}, {game} in {new}. That's quality. Well done!",
)
_TOP_Q_MEAN = (
    "{name}, {new} on {game} is good for you. For you.",
    "{name}, {game} in {new}. Top quarter of your history. Low bar, but sure.",
    "{name}, {new} on {game}. Good, by your standards. Your standards, mind.",
    "{name}, one of your better {game}s ({new}). Still not a PB though, is it.",
    "{name}, {game} {new}. Above your average. Your average is the problem.",
    "{name}, {new} on {game} — decent. Don't make a song and dance about it.",
    "{name}, top quartile {game} ({new}). Somebody had to beat your other 75%.",
    "{name}, {game} in {new}. Good. Not great. Good.",
)
_VS_USUAL_BETTER_SUPPORTIVE = (
    "{name}, {new} on {game} beats your usual {usual}! You're improving so much.",
    "{name}! {game} in {new} vs a usual {usual}. Brilliant work today!",
    "{name}, better than your normal {usual} on {game} ({new}). Love to see it!",
    "{name}, {new} on {game}, ahead of your {usual}. You're on the up!",
    "{name}, {game} {new} (usually {usual}). Keep this energy!",
    "{name}! {new} on {game} — better than usual ({usual}). Proud of you!",
    "{name}, {game} in {new}, beating your {usual} norm. Fantastic.",
    "{name}, {new} vs a usual {usual} on {game}. You're getting so good at this!",
)
_VS_USUAL_BETTER_MEAN = (
    "{name}, {new} on {game}. Better than your usual {usual}. Your usual is slow, to be fair.",
    "{name}, {game} in {new} (usually {usual}). Finally pulling your weight.",
    "{name}, beat your {usual} median on {game} with {new}. Took a while.",
    "{name}, {new} on {game} vs {usual}. Even you have good days.",
    "{name}, {game} {new}. Better than your typical {usual}. Low bar, cleared.",
    "{name}, {new} on {game} (normally {usual}). Don't expect a medal.",
    "{name}, {game} in {new} beats your usual {usual}. Lucky day?",
    "{name}, above your {usual} norm on {game} with {new}. Mark the calendar, it's rare.",
)
_VS_USUAL_PAR_SUPPORTIVE = (
    "{name}, {new} on {game}, right on your usual {usual}. Rock solid. Love it.",
    "{name}! Consistent as ever on {game}: {new}. That reliability is a superpower.",
    "{name}, {game} in {new} — your usual {usual}. Steady and brilliant.",
    "{name}, {new} on {game}. Dependable every single day. Proud of you.",
    "{name}, bang on your {usual} norm on {game} ({new}). Consistency wins weeks!",
)
_VS_USUAL_PAR_MEAN = (
    "{name}, {new} on {game}. Exactly your usual. Thrilling.",
    "{name}, {game} in {new}, same as always ({usual}). Any chance of improving?",
    "{name}, {new} on {game}. Average. As usual. As ever.",
    "{name}, {game} {new}. Your usual {usual}. The definition of 'fine'.",
    "{name}, {new} on {game}. Same old. Ever thought of trying harder?",
)
_FIRST_TODAY_SUPPORTIVE = (
    "{name}, first on {game} today with {new}! Love the enthusiasm!",
    "{name}! Leading off {game} at {new}. What a way to start the day!",
    "{name}, first {game} in ({new}) — setting the tone. Brilliant!",
    "{name}, {new} on {game}, first of the day. You're an inspiration to us all.",
    "Go {name}! First {game} on the board: {new}. Great start!",
    "{name}, up early and smashing {game}: {new}. Love it!",
    "{name}, first {game} today, {new}. You set the bar, now let's see the rest!",
    "{name}! {game} in {new}, first in. Superstar energy.",
)
_FIRST_TODAY_MEAN = (
    "{name}, first {game} today ({new}). Leading a field of one. Impressive.",
    "{name}, {new} on {game}. First! Also last. Also middle. Only you've played.",
    "{name} gets the first {game} in ({new}). Don't you have a job?",
    "{name}, first on {game} at {new}. Enjoy the lead. It's very temporary.",
    "{name}, {game} {new}, first in. Eager, aren't we.",
    "{name}, first {game} of the day ({new}). Someone will beat that by lunch.",
    "{name}, top of {game} with {new}, by default. Wins don't come cheaper.",
    "{name}, first on {game} ({new}). The others are still asleep. Low competition.",
)
_NTH_BEST_SUPPORTIVE = (
    "{name}, your {rank} best {game} ever: {new}! You're in brilliant form.",
    "{name}! {new} on {game} — {rank} best of all time. So well done.",
    "{name}, {game} in {new}, your {rank} best. A PB is coming. We can feel it!",
    "{name}, {rank}-best {game} ever ({new}). Fantastic work!",
    "{name}, {new} on {game} ranks {rank} in your history. Proud of you!",
)
_NTH_BEST_MEAN = (
    "{name}, {rank} best {game} ever ({new}). So not the best, then.",
    "{name}, {new} on {game}. Your {rank} best. Nobody remembers {rank} place.",
    "{name}, {game} in {new}, {rank} best. Close, but no PB.",
    "{name}, {rank}-best {game} ({new}). Almost good enough.",
    "{name}, {new} on {game}: {rank} best ever. Choked it at the end, did we?",
)
_BRAG_MEAN = (
    "{sender} is bragging again. Someone humble them, please.",
    "{sender} pressed brag. Quick, everyone post better scores.",
    "{sender} thinks they're good today. Prove them wrong.",
    "Brag from {sender}. Let's see how long that lasts.",
    "{sender} is showing off. It's unbecoming, {sender}.",
    "{sender} wants applause. {sender} gets this message instead.",
    "{sender} is bragging. Statistically, a collapse is due.",
    "{sender} is feeling smug. It's a good look on nobody.",
)

NEW_PB = NEW_PB + _NEW_PB_SUPPORTIVE + _NEW_PB_MEAN
TIED_PB = TIED_PB + _TIED_PB_SUPPORTIVE + _TIED_PB_MEAN
NEW_WORST = NEW_WORST + _NEW_WORST_COMFORT + _NEW_WORST_SUPPORTIVE
TIED_WORST = TIED_WORST + _TIED_WORST_COMFORT + _TIED_WORST_SUPPORTIVE
BEST_OF_DAY = BEST_OF_DAY + _BEST_OF_DAY_SUPPORTIVE + _BEST_OF_DAY_MEAN
WORST_OF_DAY = WORST_OF_DAY + _WORST_OF_DAY_COMFORT + _WORST_OF_DAY_SUPPORTIVE
ALL_TIME_RECORD = ALL_TIME_RECORD + _ALL_TIME_RECORD_SUPPORTIVE + _ALL_TIME_RECORD_MEAN
ALL_TIME_ANTI_RECORD = ALL_TIME_ANTI_RECORD + _ANTI_RECORD_COMFORT + _ANTI_RECORD_SUPPORTIVE
DOW_PB = DOW_PB + _DOW_PB_SUPPORTIVE + _DOW_PB_MEAN
DOW_WORST = DOW_WORST + _DOW_WORST_COMFORT + _DOW_WORST_SUPPORTIVE
YEAR_PB = YEAR_PB + _YEAR_PB_SUPPORTIVE + _YEAR_PB_MEAN
FIRST_TODAY = FIRST_TODAY + _FIRST_TODAY_SUPPORTIVE + _FIRST_TODAY_MEAN
NTH_BEST = NTH_BEST + _NTH_BEST_SUPPORTIVE + _NTH_BEST_MEAN
TOP_QUARTILE = TOP_QUARTILE + _TOP_Q_SUPPORTIVE + _TOP_Q_MEAN
BOTTOM_QUARTILE = BOTTOM_QUARTILE + _BOTTOM_Q_COMFORT + _BOTTOM_Q_SUPPORTIVE
DOW_BOTTOM_QUARTILE = DOW_BOTTOM_QUARTILE + _DOW_BOTTOM_Q_COMFORT + _DOW_BOTTOM_Q_SUPPORTIVE
ABOVE_FLOOR_TODAY = ABOVE_FLOOR_TODAY + _ABOVE_FLOOR_COMFORT + _ABOVE_FLOOR_SUPPORTIVE
VS_USUAL_BETTER = VS_USUAL_BETTER + _VS_USUAL_BETTER_SUPPORTIVE + _VS_USUAL_BETTER_MEAN
VS_USUAL_WORSE = VS_USUAL_WORSE + _VS_USUAL_WORSE_COMFORT + _VS_USUAL_WORSE_SUPPORTIVE
VS_USUAL_PAR = VS_USUAL_PAR + _VS_USUAL_PAR_SUPPORTIVE + _VS_USUAL_PAR_MEAN
MORNING_OPENERS_NO_PROGRESS = MORNING_OPENERS_NO_PROGRESS + _MORNING_NO_PROGRESS_SUPPORTIVE
MORNING_OPENERS_PARTIAL = MORNING_OPENERS_PARTIAL + _MORNING_PARTIAL_SUPPORTIVE
PRE_RESET_2H = PRE_RESET_2H + _PRE_RESET_2H_SUPPORTIVE
PRE_RESET_1H = PRE_RESET_1H + _PRE_RESET_1H_SUPPORTIVE
PRE_RESET_30MIN = PRE_RESET_30MIN + _PRE_RESET_30MIN_SUPPORTIVE
PRE_RESET_5MIN = PRE_RESET_5MIN + _PRE_RESET_5MIN_SUPPORTIVE
BRAG = BRAG + _BRAG_MEAN
GRIPE = GRIPE + _GRIPE_COMFORT + _GRIPE_SUPPORTIVE
MISSING_TODAY_SINGULAR = MISSING_TODAY_SINGULAR + _MISSING_SINGULAR_COMFORT
MISSING_TODAY_PLURAL = MISSING_TODAY_PLURAL + _MISSING_PLURAL_COMFORT
