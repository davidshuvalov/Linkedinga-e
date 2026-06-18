"""Data-access layer.

Defines a :class:`Repository` protocol so the webhook handler depends on an
interface, not on Supabase directly. Two implementations are provided:

- :class:`SupabaseRepository` — production; talks to Supabase via
  ``supabase-py``.
- :class:`InMemoryRepository` — used by tests and as a graceful fallback
  when ``SUPABASE_URL`` / ``SUPABASE_KEY`` are not configured, so the app
  still boots for local smoke testing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple


@dataclass(frozen=True)
class Group:
    """A self-contained leaderboard.

    ``name`` keeps the original casing (used in replies); ``name_lower``
    is the case-insensitive uniqueness key (computed app-side).
    ``recap_to`` is the optional per-group WhatsApp group post target
    used by the sender; when ``None``, recaps fan out as DMs only.
    """

    id: int
    name: str
    name_lower: str
    recap_to: Optional[str] = None
    enabled_games: Optional[frozenset] = None


@dataclass(frozen=True)
class Player:
    id: int
    whatsapp_id: str
    display_name: str
    # Opt-out flag for daily/weekly recap DMs. Defaults to ``True`` so
    # existing rows (and fresh signups) get recaps unless the player
    # runs ``notify off``. Stored as a column on ``players``.
    notifications_enabled: bool = True
    # Current group the player is signed into. ``None`` for brand-new
    # players who haven't run ``group <name>`` yet — the webhook's
    # onboarding gate uses this as the "needs to pick a group" signal.
    group_id: Optional[int] = None


@dataclass(frozen=True)
class ScoreRow:
    """A score row with the player's display name joined in.

    This is the shape consumed by ``app.scoring`` / ``app.scheduler``. The
    repository layer is responsible for the join so downstream code doesn't
    need to know about player tables.
    """

    player_id: int
    player_name: str
    game: str
    puzzle_no: int
    puzzle_date: date
    raw_score: int
    is_np: bool = False


@dataclass(frozen=True)
class Badge:
    id: int
    player_id: int
    group_id: int
    badge_kind: str
    game: Optional[str]
    earned_at: datetime
    notified: bool


class Repository(Protocol):
    """Interface used by the webhook handler and the scoring CLI."""

    def get_or_create_player(
        self, whatsapp_id: str, display_name: str
    ) -> Player: ...

    # ---- groups ----------------------------------------------------------

    def get_or_create_group(self, name: str) -> "Group":
        """Find a group by case-insensitive name; create if missing.

        ``name`` is stored verbatim for display; the lookup key is
        ``lower(name)``. Trim whitespace before passing in.
        """
        ...

    def find_group_by_name(self, name: str) -> Optional["Group"]:
        """Return the group whose ``name_lower`` equals ``lower(name)``,
        or ``None``. Used by ``switch`` to refuse silently creating a
        new group on typo."""
        ...

    def get_group(self, group_id: int) -> Optional["Group"]:
        """Resolve a group by id. Returns ``None`` if missing — callers
        treat that as "player needs to onboard again"."""
        ...

    def list_groups(self) -> List["Group"]:
        """Return every group. Used by scheduled jobs to iterate the
        cron callable over all groups."""
        ...

    def set_player_group(self, player_id: int, group_id: int) -> None:
        """Move ``player_id`` to ``group_id``. Existing scores stay in
        whichever group they were submitted under — this only changes
        where *future* submissions land."""
        ...

    def list_players_in_group(self, group_id: int) -> List[Player]:
        """Return every player currently signed into ``group_id``.
        Used by the morning nudge / pre-reset jobs to fan out per
        group."""
        ...

    def set_group_games(self, group_id: int, games: Optional[frozenset]) -> None:
        """Set the enabled games for ``group_id``.

        Pass ``None`` to remove the override and fall back to the global
        ``Settings.enabled_games`` value. Games are stored as a frozenset
        of game keys (e.g. ``frozenset({'queens', 'zip', 'tango'})``).
        """
        ...

    # ---- scores / recaps -------------------------------------------------

    def insert_score(
        self,
        *,
        player_id: int,
        group_id: int,
        game: str,
        puzzle_no: int,
        puzzle_date: date,
        raw_score: int,
        share_text: str,
    ) -> bool:
        """Insert a new score.

        Returns ``True`` on successful insert, ``False`` if a row with the
        same ``(player_id, game, puzzle_no)`` already exists. Dedup is
        intentionally per-player (not per-group) — a player can't
        legitimately submit the same puzzle twice even after switching
        groups.
        """
        ...

    def log_unparsed(self, whatsapp_id: str, body: str) -> None: ...

    def list_scores(
        self,
        *,
        date_from: date,
        date_to: date,
        group_id: int,
        game: Optional[str] = None,
    ) -> List[ScoreRow]:
        """Return all scores in ``group_id`` whose ``puzzle_date`` falls
        in ``[date_from, date_to]`` inclusive, with each player's
        display name joined in. Used to build daily recaps and weekly
        wraps. Pass ``game`` to restrict to a single game (avoids the
        PostgREST 1000-row default cap on wide date ranges).
        """
        ...

    def list_active_whatsapp_ids(
        self,
        *,
        date_from: date,
        date_to: date,
        group_id: int,
    ) -> List[str]:
        """Return ``whatsapp_id`` for every player in ``group_id`` who
        submitted at least one score in ``[date_from, date_to]``. Used
        as the DM-fallback audience when the Twilio group post fails.
        """
        ...

    def get_existing_score(
        self,
        *,
        player_id: int,
        game: str,
        puzzle_no: int,
    ) -> Optional[int]:
        """Return ``raw_score`` for an existing submission, or ``None``.
        Not group-scoped — dedup is per-player."""
        ...

    def list_player_scores(
        self, player_id: int, *, group_id: int
    ) -> List[ScoreRow]:
        """Return scores for ``player_id`` within ``group_id`` (all-time
        in that group). Per the group spec, scores stay in whichever
        group they were earned in, so PB / stats / vs only consider
        rows the player accumulated in their *current* group."""
        ...

    def list_recent_unparsed(self, *, limit: int = 10) -> List[Dict[str, Any]]:
        """Return the most recent unparsed messages (newest first).
        Not group-scoped — admin debug bucket."""
        ...

    def delete_score(
        self,
        *,
        player_id: int,
        game: str,
        puzzle_no: int,
    ) -> bool:
        """Delete a specific score submission. Returns ``True`` if a row
        was removed, ``False`` if nothing matched. Callers should check
        that the target row is still within the current LA puzzle day
        before invoking — deleting older submissions would retroactively
        rewrite past recaps. Not group-scoped: dedup is per-player so
        only one row can match anyway."""
        ...

    def update_player_name(
        self, player_id: int, display_name: str
    ) -> None:
        """Rename an existing player. Used by the ``name <new>`` command
        so the WhatsApp profile name (which the bot captures on first
        submission) can be overridden by something friendlier."""
        ...

    def set_notifications_enabled(
        self, player_id: int, enabled: bool
    ) -> None:
        """Toggle whether recap DMs go to this player. The sender's
        audience query (:func:`list_active_whatsapp_ids`) filters out
        players with the flag set to ``False``."""
        ...

    def list_players_active_since(
        self, since: date, *, group_id: int
    ) -> List[Player]:
        """Return :class:`Player` rows in ``group_id`` for everyone who
        submitted at least one score on or after ``since``. Used by the
        morning nudge job to identify "regulars" (players who'd
        typically be playing this week) and by the early-recap-fire
        check to decide who counts toward "everyone's done"."""
        ...

    def has_recap_been_sent(
        self, recap_date: date, recap_type: str, *, group_id: int
    ) -> bool:
        """Has a recap of ``recap_type`` (``"daily"`` or ``"weekly"``)
        been sent for ``recap_date`` in ``group_id`` already? Used by
        the scheduled cron to skip a day that's already had its
        early-fire recap, and by the early-fire path to avoid
        double-sending."""
        ...

    def mark_recap_sent(
        self, recap_date: date, recap_type: str, *, group_id: int
    ) -> None:
        """Record that a recap of ``recap_type`` has been sent for
        ``recap_date`` in ``group_id``. Idempotent — calling twice for
        the same (group, date, type) tuple is a no-op."""
        ...

    def has_taunted_today(
        self, player_id: int, kind: str, day: date, *, group_id: int
    ) -> bool:
        """Has ``player_id`` already used the ``kind`` taunt
        (``"brag"`` or ``"gripe"``) on ``day`` (LA) within ``group_id``?
        Drives the per-day per-command per-group cooldown so a single
        player can't blast the group with the same prompt 50 times in
        a row."""
        ...

    def record_taunt(
        self, player_id: int, kind: str, day: date, *, group_id: int
    ) -> None:
        """Record that ``player_id`` used the ``kind`` taunt on
        ``day`` within ``group_id``. Idempotent."""
        ...

    def list_today_for_game(
        self, *, game: str, day: date, group_id: int
    ) -> List[ScoreRow]:
        """Return every score row for ``game`` on ``day`` within
        ``group_id``. Used by submission-time trigger detection
        ("best of today" / "worst of today") so the writer can rank
        the just-inserted row against teammates on the same day."""
        ...

    def get_top_extremes_for_game(
        self, *, game: str, n: int = 2, group_id: int
    ) -> tuple[List["ScoreRow"], List["ScoreRow"]]:
        """Return ``(fastest_top_n, slowest_top_n)`` score rows for
        ``game`` within ``group_id``, all time. Each list is ordered
        most-extreme-first and contains up to ``n`` rows (fewer if
        the group has fewer total submissions). Caller uses these
        to detect "all-time record" / "all-time worst-ever"
        triggers after an insert — by asking for top-2 the caller
        can always identify the "previous holder" even when the
        just-inserted row claims first place. Group-scoped so a
        new group's first submission isn't compared against a legacy
        group's records."""
        ...

    def get_top_extremes_for_game_dow(
        self, *, game: str, weekday: int, n: int = 2, group_id: int
    ) -> List["ScoreRow"]:
        """Return the up to ``n`` fastest score rows for ``game``
        within ``group_id`` on ``weekday`` (0=Mon…6=Sun), all time.
        Sorted fastest-first. Used to detect group DOW record
        triggers after an insert."""
        ...

    # ---- badges ----------------------------------------------------------

    def award_badge(
        self,
        *,
        player_id: int,
        group_id: int,
        badge_kind: str,
        game: Optional[str],
        earned_at: datetime,
    ) -> bool:
        """Insert a badge row. Returns ``True`` if this is the first time
        the badge is awarded, ``False`` if the player already holds it
        (idempotent). Raises on unexpected DB errors."""
        ...

    def list_unnotified_badges(
        self, *, player_id: int, group_id: int
    ) -> List["Badge"]:
        """Return all badge rows for the player in the group where
        ``notified`` is False."""
        ...

    def mark_badges_notified(self, badge_ids: List[int]) -> None:
        """Set ``notified=True`` on the given badge ids. Idempotent."""
        ...

    def list_player_badges(
        self, *, player_id: int, group_id: int
    ) -> List["Badge"]:
        """Return all earned badges for a player (for display)."""
        ...


# ---------------------------------------------------------------------------
# In-memory implementation (tests + local fallback)
# ---------------------------------------------------------------------------


@dataclass
class InMemoryRepository:
    """Simple dict-backed repo. Not thread-safe, not persistent."""

    _players: Dict[str, Player] = field(default_factory=dict)
    _next_player_id: int = 1
    _groups: Dict[int, Group] = field(default_factory=dict)
    _groups_by_lower: Dict[str, int] = field(default_factory=dict)
    _next_group_id: int = 1
    _score_keys: set = field(default_factory=set)
    scores: List[Dict[str, Any]] = field(default_factory=list)
    unparsed: List[Dict[str, Any]] = field(default_factory=list)
    # (group_id, date, type) → sent. Stores a set since the only thing
    # we ever ask is "has this tuple been recorded?".
    _recap_sent: set = field(default_factory=set)
    # (group_id, player_id, kind, day) → recorded. Drives the per-day
    # per-command per-group cooldown for the brag/gripe Easter eggs.
    _taunt_log: set = field(default_factory=set)
    # badge_id → Badge; (player_id, group_id, kind, game) → badge_id
    _badges: Dict[int, "Badge"] = field(default_factory=dict)
    _badges_by_key: Dict[tuple, int] = field(default_factory=dict)

    # ---- groups ----------------------------------------------------------

    def get_or_create_group(self, name: str) -> Group:
        key = name.lower()
        existing_id = self._groups_by_lower.get(key)
        if existing_id is not None:
            return self._groups[existing_id]
        group = Group(id=self._next_group_id, name=name, name_lower=key)
        self._next_group_id += 1
        self._groups[group.id] = group
        self._groups_by_lower[key] = group.id
        return group

    def find_group_by_name(self, name: str) -> Optional[Group]:
        gid = self._groups_by_lower.get(name.lower())
        if gid is None:
            return None
        return self._groups[gid]

    def get_group(self, group_id: int) -> Optional[Group]:
        return self._groups.get(group_id)

    def list_groups(self) -> List[Group]:
        return [self._groups[gid] for gid in sorted(self._groups)]

    def set_player_group(self, player_id: int, group_id: int) -> None:
        existing = self._find_player_by_id(player_id)
        if existing is None:
            return
        updated = Player(
            id=existing.id,
            whatsapp_id=existing.whatsapp_id,
            display_name=existing.display_name,
            notifications_enabled=existing.notifications_enabled,
            group_id=group_id,
        )
        self._players[existing.whatsapp_id] = updated

    def list_players_in_group(self, group_id: int) -> List[Player]:
        return [
            p for p in sorted(self._players.values(), key=lambda p: p.id)
            if p.group_id == group_id
        ]

    def set_group_games(self, group_id: int, games: Optional[frozenset]) -> None:
        existing = self._groups.get(group_id)
        if existing is None:
            return
        updated = Group(
            id=existing.id,
            name=existing.name,
            name_lower=existing.name_lower,
            recap_to=existing.recap_to,
            enabled_games=frozenset(games) if games else None,
        )
        self._groups[group_id] = updated

    # ---- players ---------------------------------------------------------

    def get_or_create_player(
        self, whatsapp_id: str, display_name: str
    ) -> Player:
        if whatsapp_id in self._players:
            return self._players[whatsapp_id]
        player = Player(
            id=self._next_player_id,
            whatsapp_id=whatsapp_id,
            display_name=display_name,
        )
        self._next_player_id += 1
        self._players[whatsapp_id] = player
        return player

    def _find_player_by_id(self, player_id: int) -> Optional[Player]:
        for p in self._players.values():
            if p.id == player_id:
                return p
        return None

    def update_player_name(
        self, player_id: int, display_name: str
    ) -> None:
        existing = self._find_player_by_id(player_id)
        if existing is None:
            return
        renamed = Player(
            id=existing.id,
            whatsapp_id=existing.whatsapp_id,
            display_name=display_name,
            notifications_enabled=existing.notifications_enabled,
            group_id=existing.group_id,
        )
        self._players[existing.whatsapp_id] = renamed

    def set_notifications_enabled(
        self, player_id: int, enabled: bool
    ) -> None:
        existing = self._find_player_by_id(player_id)
        if existing is None:
            return
        updated = Player(
            id=existing.id,
            whatsapp_id=existing.whatsapp_id,
            display_name=existing.display_name,
            notifications_enabled=enabled,
            group_id=existing.group_id,
        )
        self._players[existing.whatsapp_id] = updated

    # ---- scores ----------------------------------------------------------

    def delete_score(
        self,
        *,
        player_id: int,
        game: str,
        puzzle_no: int,
    ) -> bool:
        key: Tuple[int, str, int] = (player_id, game, puzzle_no)
        if key not in self._score_keys:
            return False
        self._score_keys.discard(key)
        self.scores = [
            s for s in self.scores
            if not (
                s["player_id"] == player_id
                and s["game"] == game
                and s["puzzle_no"] == puzzle_no
            )
        ]
        return True

    def insert_score(
        self,
        *,
        player_id: int,
        group_id: int,
        game: str,
        puzzle_no: int,
        puzzle_date: date,
        raw_score: int,
        share_text: str,
    ) -> bool:
        key: Tuple[int, str, int] = (player_id, game, puzzle_no)
        if key in self._score_keys:
            return False
        self._score_keys.add(key)
        self.scores.append(
            {
                "player_id": player_id,
                "group_id": group_id,
                "game": game,
                "puzzle_no": puzzle_no,
                "puzzle_date": puzzle_date,
                "raw_score": raw_score,
                "share_text": share_text,
            }
        )
        return True

    def log_unparsed(self, whatsapp_id: str, body: str) -> None:
        self.unparsed.append({"whatsapp_id": whatsapp_id, "body": body})

    def _row(self, s: Dict[str, Any], names_by_id: Dict[int, str]) -> ScoreRow:
        return ScoreRow(
            player_id=s["player_id"],
            player_name=names_by_id.get(s["player_id"], ""),
            game=s["game"],
            puzzle_no=s["puzzle_no"],
            puzzle_date=s["puzzle_date"],
            raw_score=s["raw_score"],
        )

    def list_scores(
        self,
        *,
        date_from: date,
        date_to: date,
        group_id: int,
        game: Optional[str] = None,
    ) -> List[ScoreRow]:
        names_by_id = {p.id: p.display_name for p in self._players.values()}
        return [
            self._row(s, names_by_id)
            for s in self.scores
            if s.get("group_id") == group_id
            and date_from <= s["puzzle_date"] <= date_to
            and (game is None or s["game"] == game)
        ]

    def list_active_whatsapp_ids(
        self,
        *,
        date_from: date,
        date_to: date,
        group_id: int,
    ) -> List[str]:
        active_pids = {
            s["player_id"]
            for s in self.scores
            if s.get("group_id") == group_id
            and date_from <= s["puzzle_date"] <= date_to
        }
        # Exclude opted-out players — the sender uses this list to
        # decide who receives the recap DM, so we honor the opt-out
        # at the audience layer.
        id_by_pid = {
            p.id: p.whatsapp_id
            for p in self._players.values()
            if p.notifications_enabled
        }
        return [id_by_pid[pid] for pid in sorted(active_pids) if pid in id_by_pid]

    def get_existing_score(
        self,
        *,
        player_id: int,
        game: str,
        puzzle_no: int,
    ) -> Optional[int]:
        for s in self.scores:
            if (
                s["player_id"] == player_id
                and s["game"] == game
                and s["puzzle_no"] == puzzle_no
            ):
                return s["raw_score"]
        return None

    def list_player_scores(
        self, player_id: int, *, group_id: int
    ) -> List[ScoreRow]:
        names_by_id = {p.id: p.display_name for p in self._players.values()}
        return [
            self._row(s, names_by_id)
            for s in self.scores
            if s["player_id"] == player_id and s.get("group_id") == group_id
        ]

    def list_recent_unparsed(self, *, limit: int = 10) -> List[Dict[str, Any]]:
        return list(reversed(self.unparsed[-limit:]))

    def list_players_active_since(
        self, since: date, *, group_id: int
    ) -> List[Player]:
        active_ids = {
            s["player_id"]
            for s in self.scores
            if s.get("group_id") == group_id and s["puzzle_date"] >= since
        }
        # Preserve player_id order so the morning-nudge sequence is
        # deterministic in tests and predictable in logs.
        by_id = {p.id: p for p in self._players.values()}
        return [by_id[pid] for pid in sorted(active_ids) if pid in by_id]

    def has_recap_been_sent(
        self, recap_date: date, recap_type: str, *, group_id: int
    ) -> bool:
        return (group_id, recap_date, recap_type) in self._recap_sent

    def mark_recap_sent(
        self, recap_date: date, recap_type: str, *, group_id: int
    ) -> None:
        self._recap_sent.add((group_id, recap_date, recap_type))

    def has_taunted_today(
        self, player_id: int, kind: str, day: date, *, group_id: int
    ) -> bool:
        return (group_id, player_id, kind, day) in self._taunt_log

    def record_taunt(
        self, player_id: int, kind: str, day: date, *, group_id: int
    ) -> None:
        self._taunt_log.add((group_id, player_id, kind, day))

    def list_today_for_game(
        self, *, game: str, day: date, group_id: int
    ) -> List[ScoreRow]:
        names_by_id = {p.id: p.display_name for p in self._players.values()}
        return [
            self._row(s, names_by_id)
            for s in self.scores
            if s.get("group_id") == group_id
            and s["game"] == game
            and s["puzzle_date"] == day
        ]

    def get_top_extremes_for_game(
        self, *, game: str, n: int = 2, group_id: int
    ) -> tuple[List[ScoreRow], List[ScoreRow]]:
        names_by_id = {p.id: p.display_name for p in self._players.values()}
        rows = [
            s for s in self.scores
            if s["game"] == game and s.get("group_id") == group_id
        ]
        if not rows:
            return [], []

        # Tiebreak on player_id so the ordering is deterministic in
        # tests when multiple rows share the same raw_score.
        fastest_sorted = sorted(rows, key=lambda s: (s["raw_score"], s["player_id"]))
        slowest_sorted = sorted(
            rows, key=lambda s: (-s["raw_score"], s["player_id"])
        )
        return (
            [self._row(s, names_by_id) for s in fastest_sorted[:n]],
            [self._row(s, names_by_id) for s in slowest_sorted[:n]],
        )

    def get_top_extremes_for_game_dow(
        self, *, game: str, weekday: int, n: int = 2, group_id: int
    ) -> List[ScoreRow]:
        names_by_id = {p.id: p.display_name for p in self._players.values()}
        rows = [
            s for s in self.scores
            if s["game"] == game
            and s.get("group_id") == group_id
            and s["puzzle_date"].weekday() == weekday
        ]
        if not rows:
            return []
        sorted_rows = sorted(rows, key=lambda s: (s["raw_score"], s["player_id"]))
        return [self._row(s, names_by_id) for s in sorted_rows[:n]]

    # ---- badges ----------------------------------------------------------

    def award_badge(
        self,
        *,
        player_id: int,
        group_id: int,
        badge_kind: str,
        game: Optional[str],
        earned_at: datetime,
    ) -> bool:
        key = (player_id, group_id, badge_kind, game)
        if key in self._badges_by_key:
            return False
        badge_id = len(self._badges) + 1
        badge = Badge(
            id=badge_id,
            player_id=player_id,
            group_id=group_id,
            badge_kind=badge_kind,
            game=game,
            earned_at=earned_at,
            notified=False,
        )
        self._badges[badge_id] = badge
        self._badges_by_key[key] = badge_id
        return True

    def list_unnotified_badges(self, *, player_id: int, group_id: int) -> List[Badge]:
        return [
            b for b in self._badges.values()
            if b.player_id == player_id and b.group_id == group_id and not b.notified
        ]

    def mark_badges_notified(self, badge_ids: List[int]) -> None:
        from dataclasses import replace as _replace
        for bid in badge_ids:
            if bid in self._badges:
                old = self._badges[bid]
                self._badges[bid] = _replace(old, notified=True)

    def list_player_badges(self, *, player_id: int, group_id: int) -> List[Badge]:
        return [
            b for b in self._badges.values()
            if b.player_id == player_id and b.group_id == group_id
        ]


# ---------------------------------------------------------------------------
# Supabase implementation
# ---------------------------------------------------------------------------


class SupabaseRepository:
    """Production repository backed by a ``supabase-py`` client.

    The client is injected rather than constructed here so that construction
    (and the ``supabase`` import) are opt-in and easy to mock.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        # ``notifications_enabled`` was added to ``players`` after the
        # initial schema. Detect once on construction so every
        # subsequent query picks the right SELECT columns — avoids
        # hitting a "column does not exist" error on every inbound
        # message if the migration hasn't been applied yet.
        self._has_notifications_column = self._detect_notifications_column()
        # Group columns landed in a later migration too. When absent,
        # the repo behaves as a single-default-group app: writes don't
        # set group_id, reads ignore the kwarg. Lets the app boot on a
        # pre-migration schema instead of dying on column-missing.
        self._has_group_columns = self._detect_group_columns()

    def _detect_notifications_column(self) -> bool:
        """Probe whether ``players.notifications_enabled`` exists.

        Returns ``False`` (and logs a warning) if the canary query
        fails — we assume that's the migration not being applied
        rather than a transient network error, because old-schema
        installs should still work gracefully.
        """
        try:
            (
                self._client.table("players")
                .select("notifications_enabled")
                .limit(1)
                .execute()
            )
            return True
        except Exception as exc:  # noqa: BLE001 — intentionally broad
            import logging

            logging.getLogger(__name__).warning(
                "players.notifications_enabled column missing (%s). "
                "Run `db/schema.sql` to enable notify on/off. Until "
                "then, all players receive recap DMs by default.",
                exc,
            )
            return False

    def _detect_group_columns(self) -> bool:
        """Probe whether the groups table + group_id columns exist."""
        try:
            (
                self._client.table("groups")
                .select("id")
                .limit(1)
                .execute()
            )
            (
                self._client.table("scores")
                .select("group_id")
                .limit(1)
                .execute()
            )
            return True
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning(
                "Group columns missing (%s). Run `db/schema.sql` to "
                "enable group isolation. Until then, every read/write "
                "behaves as a single shared leaderboard.",
                exc,
            )
            return False

    def _player_select_cols(self) -> str:
        """SELECT column list for reads against ``players`` — omits
        ``notifications_enabled`` / ``group_id`` on old schemas so the
        query doesn't fail with a column-does-not-exist error."""
        base = "id, whatsapp_id, display_name"
        if self._has_notifications_column:
            base += ", notifications_enabled"
        if self._has_group_columns:
            base += ", group_id"
        return base

    def _row_to_player(self, row: Dict[str, Any]) -> Player:
        # ``notifications_enabled`` and ``group_id`` are recent
        # columns; fall back to defaults when missing so old schemas
        # still load.
        return Player(
            id=row["id"],
            whatsapp_id=row["whatsapp_id"],
            display_name=row["display_name"],
            notifications_enabled=row.get("notifications_enabled", True),
            group_id=row.get("group_id"),
        )

    def _row_to_group(self, row: Dict[str, Any]) -> Group:
        import json
        raw_games = row.get("enabled_games")
        enabled_games: Optional[frozenset] = None
        if raw_games:
            try:
                enabled_games = frozenset(json.loads(raw_games))
            except Exception:
                pass
        return Group(
            id=row["id"],
            name=row["name"],
            name_lower=row["name_lower"],
            recap_to=row.get("recap_to"),
            enabled_games=enabled_games,
        )

    def get_or_create_group(self, name: str) -> Group:
        if not self._has_group_columns:
            raise RuntimeError(
                "groups table missing — run db/schema.sql migration "
                "before using group commands"
            )
        key = name.lower()
        resp = (
            self._client.table("groups")
            .select("id, name, name_lower, recap_to, enabled_games")
            .eq("name_lower", key)
            .limit(1)
            .execute()
        )
        if resp.data:
            return self._row_to_group(resp.data[0])
        inserted = (
            self._client.table("groups")
            .insert({"name": name, "name_lower": key})
            .execute()
        )
        return self._row_to_group(inserted.data[0])

    def find_group_by_name(self, name: str) -> Optional[Group]:
        if not self._has_group_columns:
            return None
        resp = (
            self._client.table("groups")
            .select("id, name, name_lower, recap_to, enabled_games")
            .eq("name_lower", name.lower())
            .limit(1)
            .execute()
        )
        if not resp.data:
            return None
        return self._row_to_group(resp.data[0])

    def get_group(self, group_id: int) -> Optional[Group]:
        if not self._has_group_columns:
            return None
        resp = (
            self._client.table("groups")
            .select("id, name, name_lower, recap_to, enabled_games")
            .eq("id", group_id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return None
        return self._row_to_group(resp.data[0])

    def list_groups(self) -> List[Group]:
        if not self._has_group_columns:
            return []
        resp = (
            self._client.table("groups")
            .select("id, name, name_lower, recap_to, enabled_games")
            .order("id")
            .execute()
        )
        return [self._row_to_group(r) for r in resp.data or []]

    def set_player_group(self, player_id: int, group_id: int) -> None:
        if not self._has_group_columns:
            raise RuntimeError(
                "players.group_id column missing — run db/schema.sql "
                "migration before using group commands"
            )
        (
            self._client.table("players")
            .update({"group_id": group_id})
            .eq("id", player_id)
            .execute()
        )

    def list_players_in_group(self, group_id: int) -> List[Player]:
        if not self._has_group_columns:
            return []
        resp = (
            self._client.table("players")
            .select(self._player_select_cols())
            .eq("group_id", group_id)
            .order("id")
            .execute()
        )
        return [self._row_to_player(r) for r in resp.data or []]

    def set_group_games(self, group_id: int, games: Optional[frozenset]) -> None:
        if not self._has_group_columns:
            raise RuntimeError(
                "groups table missing — run schema migration before using group commands"
            )
        import json
        value = json.dumps(sorted(games)) if games else None
        (
            self._client.table("groups")
            .update({"enabled_games": value})
            .eq("id", group_id)
            .execute()
        )

    def get_or_create_player(
        self, whatsapp_id: str, display_name: str
    ) -> Player:
        resp = (
            self._client.table("players")
            .select(self._player_select_cols())
            .eq("whatsapp_id", whatsapp_id)
            .limit(1)
            .execute()
        )
        if resp.data:
            return self._row_to_player(resp.data[0])
        inserted = (
            self._client.table("players")
            .insert(
                {"whatsapp_id": whatsapp_id, "display_name": display_name}
            )
            .execute()
        )
        return self._row_to_player(inserted.data[0])

    def update_player_name(
        self, player_id: int, display_name: str
    ) -> None:
        (
            self._client.table("players")
            .update({"display_name": display_name})
            .eq("id", player_id)
            .execute()
        )

    def set_notifications_enabled(
        self, player_id: int, enabled: bool
    ) -> None:
        if not self._has_notifications_column:
            raise RuntimeError(
                "players.notifications_enabled column missing — run "
                "db/schema.sql migration before using notify on/off"
            )
        (
            self._client.table("players")
            .update({"notifications_enabled": enabled})
            .eq("id", player_id)
            .execute()
        )

    def delete_score(
        self,
        *,
        player_id: int,
        game: str,
        puzzle_no: int,
    ) -> bool:
        # Check first so we can distinguish "no such row" from a
        # successful delete — Supabase's delete response doesn't
        # surface a row count in every client version.
        existing = (
            self._client.table("scores")
            .select("id")
            .eq("player_id", player_id)
            .eq("game", game)
            .eq("puzzle_no", puzzle_no)
            .limit(1)
            .execute()
        )
        if not existing.data:
            return False
        (
            self._client.table("scores")
            .delete()
            .eq("player_id", player_id)
            .eq("game", game)
            .eq("puzzle_no", puzzle_no)
            .execute()
        )
        return True

    def insert_score(
        self,
        *,
        player_id: int,
        group_id: int,
        game: str,
        puzzle_no: int,
        puzzle_date: date,
        raw_score: int,
        share_text: str,
    ) -> bool:
        # Check first — cheaper than eating a constraint violation and the
        # race window is microseconds for a 6–12 person group.
        existing = (
            self._client.table("scores")
            .select("id")
            .eq("player_id", player_id)
            .eq("game", game)
            .eq("puzzle_no", puzzle_no)
            .limit(1)
            .execute()
        )
        if existing.data:
            return False
        payload: Dict[str, Any] = {
            "player_id": player_id,
            "game": game,
            "puzzle_no": puzzle_no,
            "puzzle_date": puzzle_date.isoformat(),
            "raw_score": raw_score,
            "share_text": share_text,
        }
        if self._has_group_columns:
            payload["group_id"] = group_id
        (
            self._client.table("scores")
            .insert(payload)
            .execute()
        )
        return True

    def log_unparsed(self, whatsapp_id: str, body: str) -> None:
        (
            self._client.table("unparsed_messages")
            .insert({"whatsapp_id": whatsapp_id, "body": body})
            .execute()
        )

    def list_scores(
        self,
        *,
        date_from: date,
        date_to: date,
        group_id: int,
        game: Optional[str] = None,
    ) -> List[ScoreRow]:
        # PostgREST embedded join: ``players(display_name)`` inlines the
        # parent row under a ``players`` key on each returned score row.
        query = (
            self._client.table("scores")
            .select(
                "player_id, game, puzzle_no, puzzle_date, raw_score, "
                "players(display_name)"
            )
            .gte("puzzle_date", date_from.isoformat())
            .lte("puzzle_date", date_to.isoformat())
        )
        if self._has_group_columns:
            query = query.eq("group_id", group_id)
        if game is not None:
            query = query.eq("game", game)
        resp = query.execute()
        rows: List[ScoreRow] = []
        for row in resp.data or []:
            player = row.get("players") or {}
            rows.append(
                ScoreRow(
                    player_id=row["player_id"],
                    player_name=player.get("display_name", ""),
                    game=row["game"],
                    puzzle_no=row["puzzle_no"],
                    puzzle_date=date.fromisoformat(row["puzzle_date"]),
                    raw_score=row["raw_score"],
                )
            )
        return rows

    def list_active_whatsapp_ids(
        self,
        *,
        date_from: date,
        date_to: date,
        group_id: int,
    ) -> List[str]:
        # Embed the notifications_enabled column only if it exists —
        # otherwise this SELECT would fail with "column does not
        # exist" and take every recap command down with it.
        embed_cols = "whatsapp_id"
        if self._has_notifications_column:
            embed_cols += ", notifications_enabled"
        query = (
            self._client.table("scores")
            .select(f"player_id, players({embed_cols})")
            .gte("puzzle_date", date_from.isoformat())
            .lte("puzzle_date", date_to.isoformat())
        )
        if self._has_group_columns:
            query = query.eq("group_id", group_id)
        resp = query.execute()
        seen: set[str] = set()
        result: List[str] = []
        for row in resp.data or []:
            player = row.get("players") or {}
            wid = player.get("whatsapp_id", "")
            # Missing column (old schema) → treat as enabled.
            if not player.get("notifications_enabled", True):
                continue
            if wid and wid not in seen:
                seen.add(wid)
                result.append(wid)
        return result

    def get_existing_score(
        self,
        *,
        player_id: int,
        game: str,
        puzzle_no: int,
    ) -> Optional[int]:
        resp = (
            self._client.table("scores")
            .select("raw_score")
            .eq("player_id", player_id)
            .eq("game", game)
            .eq("puzzle_no", puzzle_no)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]["raw_score"]
        return None

    def list_player_scores(
        self, player_id: int, *, group_id: int
    ) -> List[ScoreRow]:
        query = (
            self._client.table("scores")
            .select(
                "player_id, game, puzzle_no, puzzle_date, raw_score, "
                "players(display_name)"
            )
            .eq("player_id", player_id)
            .order("puzzle_date", desc=True)
        )
        if self._has_group_columns:
            query = query.eq("group_id", group_id)
        resp = query.execute()
        rows: List[ScoreRow] = []
        for row in resp.data or []:
            player = row.get("players") or {}
            rows.append(
                ScoreRow(
                    player_id=row["player_id"],
                    player_name=player.get("display_name", ""),
                    game=row["game"],
                    puzzle_no=row["puzzle_no"],
                    puzzle_date=date.fromisoformat(row["puzzle_date"]),
                    raw_score=row["raw_score"],
                )
            )
        return rows

    def list_recent_unparsed(self, *, limit: int = 10) -> List[Dict[str, Any]]:
        resp = (
            self._client.table("unparsed_messages")
            .select("whatsapp_id, body, created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []

    def list_players_active_since(
        self, since: date, *, group_id: int
    ) -> List[Player]:
        # Two queries on purpose: first the distinct active player_ids
        # for the date window, then the players themselves. PostgREST
        # doesn't support DISTINCT in the embed selector, so doing it
        # in Python avoids a giant deduped JSON payload.
        query = (
            self._client.table("scores")
            .select("player_id")
            .gte("puzzle_date", since.isoformat())
        )
        if self._has_group_columns:
            query = query.eq("group_id", group_id)
        scores_resp = query.execute()
        active_ids = sorted({row["player_id"] for row in scores_resp.data or []})
        if not active_ids:
            return []
        players_resp = (
            self._client.table("players")
            .select(self._player_select_cols())
            .in_("id", active_ids)
            .execute()
        )
        return [self._row_to_player(r) for r in players_resp.data or []]

    def has_recap_been_sent(
        self, recap_date: date, recap_type: str, *, group_id: int
    ) -> bool:
        query = (
            self._client.table("recap_log")
            .select("id")
            .eq("recap_date", recap_date.isoformat())
            .eq("recap_type", recap_type)
            .limit(1)
        )
        if self._has_group_columns:
            query = query.eq("group_id", group_id)
        resp = query.execute()
        return bool(resp.data)

    def mark_recap_sent(
        self, recap_date: date, recap_type: str, *, group_id: int
    ) -> None:
        # Pre-check rather than relying on the unique constraint —
        # supabase-py surfaces conflict errors as raised exceptions
        # and we want this method to be quietly idempotent.
        if self.has_recap_been_sent(recap_date, recap_type, group_id=group_id):
            return
        payload: Dict[str, Any] = {
            "recap_date": recap_date.isoformat(),
            "recap_type": recap_type,
        }
        if self._has_group_columns:
            payload["group_id"] = group_id
        (
            self._client.table("recap_log")
            .insert(payload)
            .execute()
        )

    # The brag/gripe Easter eggs piggy-back on ``recap_log`` so we
    # don't need a new table or migration to ship them. The
    # ``recap_type`` column is just a string, so we type the key as
    # ``taunt:<kind>:<player_id>`` to namespace it cleanly away from
    # the daily/weekly recap entries.
    @staticmethod
    def _taunt_key(player_id: int, kind: str) -> str:
        return f"taunt:{kind}:{player_id}"

    def has_taunted_today(
        self, player_id: int, kind: str, day: date, *, group_id: int
    ) -> bool:
        return self.has_recap_been_sent(
            day, self._taunt_key(player_id, kind), group_id=group_id
        )

    def record_taunt(
        self, player_id: int, kind: str, day: date, *, group_id: int
    ) -> None:
        self.mark_recap_sent(
            day, self._taunt_key(player_id, kind), group_id=group_id
        )

    def list_today_for_game(
        self, *, game: str, day: date, group_id: int
    ) -> List[ScoreRow]:
        # Embed the players(display_name) join so we can render the
        # caller's "you beat X by N seconds" copy without a second
        # lookup. ``puzzle_date`` is stored as ISO; eq-match works
        # natively against the date.isoformat() string.
        query = (
            self._client.table("scores")
            .select(
                "player_id, game, puzzle_no, puzzle_date, raw_score, "
                "players(display_name)"
            )
            .eq("game", game)
            .eq("puzzle_date", day.isoformat())
        )
        if self._has_group_columns:
            query = query.eq("group_id", group_id)
        resp = query.execute()
        rows: List[ScoreRow] = []
        for row in resp.data or []:
            player = row.get("players") or {}
            rows.append(
                ScoreRow(
                    player_id=row["player_id"],
                    player_name=player.get("display_name", ""),
                    game=row["game"],
                    puzzle_no=row["puzzle_no"],
                    puzzle_date=date.fromisoformat(row["puzzle_date"]),
                    raw_score=row["raw_score"],
                )
            )
        return rows

    def get_top_extremes_for_game(
        self, *, game: str, n: int = 2, group_id: int
    ) -> tuple[List[ScoreRow], List[ScoreRow]]:
        # Two cheap queries (order + limit n each) instead of pulling
        # the whole game's history. PostgREST honours .order() so
        # this lands at most n rows per side.
        common = (
            "player_id, game, puzzle_no, puzzle_date, raw_score, "
            "players(display_name)"
        )
        fastest_q = (
            self._client.table("scores")
            .select(common)
            .eq("game", game)
            .order("raw_score")
            .limit(n)
        )
        slowest_q = (
            self._client.table("scores")
            .select(common)
            .eq("game", game)
            .order("raw_score", desc=True)
            .limit(n)
        )
        if self._has_group_columns:
            fastest_q = fastest_q.eq("group_id", group_id)
            slowest_q = slowest_q.eq("group_id", group_id)
        fastest_resp = fastest_q.execute()
        slowest_resp = slowest_q.execute()

        def _rows(resp_data: Optional[List[Dict[str, Any]]]) -> List[ScoreRow]:
            out: List[ScoreRow] = []
            for row in resp_data or []:
                player = row.get("players") or {}
                out.append(
                    ScoreRow(
                        player_id=row["player_id"],
                        player_name=player.get("display_name", ""),
                        game=row["game"],
                        puzzle_no=row["puzzle_no"],
                        puzzle_date=date.fromisoformat(row["puzzle_date"]),
                        raw_score=row["raw_score"],
                    )
                )
            return out

        return _rows(fastest_resp.data), _rows(slowest_resp.data)

    def get_top_extremes_for_game_dow(
        self, *, game: str, weekday: int, n: int = 2, group_id: int
    ) -> List[ScoreRow]:
        # PostgREST can't filter by EXTRACT(DOW), so pull all rows for
        # game + group and filter by weekday in Python. Fine for small groups.
        query = (
            self._client.table("scores")
            .select(
                "player_id, game, puzzle_no, puzzle_date, raw_score, "
                "players(display_name)"
            )
            .eq("game", game)
        )
        if self._has_group_columns:
            query = query.eq("group_id", group_id)
        resp = query.execute()
        rows: List[ScoreRow] = []
        for row in resp.data or []:
            d = date.fromisoformat(row["puzzle_date"])
            if d.weekday() != weekday:
                continue
            player = row.get("players") or {}
            rows.append(
                ScoreRow(
                    player_id=row["player_id"],
                    player_name=player.get("display_name", ""),
                    game=row["game"],
                    puzzle_no=row["puzzle_no"],
                    puzzle_date=d,
                    raw_score=row["raw_score"],
                )
            )
        rows.sort(key=lambda r: (r.raw_score, r.player_id))
        return rows[:n]

    # ---- badges ----------------------------------------------------------

    def award_badge(
        self,
        *,
        player_id: int,
        group_id: int,
        badge_kind: str,
        game: Optional[str],
        earned_at: datetime,
    ) -> bool:
        row = {"player_id": player_id, "group_id": group_id,
               "badge_kind": badge_kind, "game": game,
               "earned_at": earned_at.isoformat(), "notified": False}
        try:
            resp = self._client.table("badges").insert(row).execute()
            return bool(resp.data)
        except Exception:
            return False  # unique constraint = already awarded

    def list_unnotified_badges(self, *, player_id: int, group_id: int) -> List[Badge]:
        resp = (
            self._client.table("badges")
            .select("*")
            .eq("player_id", player_id)
            .eq("group_id", group_id)
            .eq("notified", False)
            .execute()
        )
        return [self._row_to_badge(r) for r in resp.data or []]

    def mark_badges_notified(self, badge_ids: List[int]) -> None:
        if not badge_ids:
            return
        self._client.table("badges").update({"notified": True}).in_("id", badge_ids).execute()

    def list_player_badges(self, *, player_id: int, group_id: int) -> List[Badge]:
        resp = (
            self._client.table("badges")
            .select("*")
            .eq("player_id", player_id)
            .eq("group_id", group_id)
            .order("earned_at")
            .execute()
        )
        return [self._row_to_badge(r) for r in resp.data or []]

    @staticmethod
    def _row_to_badge(row: Dict[str, Any]) -> Badge:
        return Badge(
            id=row["id"],
            player_id=row["player_id"],
            group_id=row["group_id"],
            badge_kind=row["badge_kind"],
            game=row.get("game"),
            earned_at=datetime.fromisoformat(row["earned_at"]),
            notified=row.get("notified", False),
        )
