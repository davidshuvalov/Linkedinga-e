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
from datetime import date
from typing import Any, Dict, List, Optional, Protocol, Tuple


@dataclass(frozen=True)
class Player:
    id: int
    whatsapp_id: str
    display_name: str
    # Opt-out flag for daily/weekly recap DMs. Defaults to ``True`` so
    # existing rows (and fresh signups) get recaps unless the player
    # runs ``notify off``. Stored as a column on ``players``.
    notifications_enabled: bool = True


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


class Repository(Protocol):
    """Interface used by the webhook handler and the scoring CLI."""

    def get_or_create_player(
        self, whatsapp_id: str, display_name: str
    ) -> Player: ...

    def insert_score(
        self,
        *,
        player_id: int,
        game: str,
        puzzle_no: int,
        puzzle_date: date,
        raw_score: int,
        share_text: str,
    ) -> bool:
        """Insert a new score.

        Returns ``True`` on successful insert, ``False`` if a row with the
        same ``(player_id, game, puzzle_no)`` already exists.
        """
        ...

    def log_unparsed(self, whatsapp_id: str, body: str) -> None: ...

    def list_scores(
        self,
        *,
        date_from: date,
        date_to: date,
    ) -> List[ScoreRow]:
        """Return all scores whose ``puzzle_date`` falls in
        ``[date_from, date_to]`` inclusive, with each player's display name
        joined in. Used to build daily recaps and weekly wraps.
        """
        ...

    def list_active_whatsapp_ids(
        self,
        *,
        date_from: date,
        date_to: date,
    ) -> List[str]:
        """Return ``whatsapp_id`` for every player who submitted at least
        one score in ``[date_from, date_to]``. Used as the DM-fallback
        audience when the Twilio group post fails.
        """
        ...

    def get_existing_score(
        self,
        *,
        player_id: int,
        game: str,
        puzzle_no: int,
    ) -> Optional[int]:
        """Return ``raw_score`` for an existing submission, or ``None``."""
        ...

    def list_player_scores(self, player_id: int) -> List[ScoreRow]:
        """Return all scores for a specific player (all-time)."""
        ...

    def list_recent_unparsed(self, *, limit: int = 10) -> List[Dict[str, Any]]:
        """Return the most recent unparsed messages (newest first)."""
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
        rewrite past recaps."""
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

    def list_players_active_since(self, since: date) -> List[Player]:
        """Return :class:`Player` rows for everyone who submitted at
        least one score on or after ``since``. Used by the morning
        nudge job to identify "regulars" (players who'd typically
        be playing this week) and by the early-recap-fire check to
        decide who counts toward "everyone's done"."""
        ...

    def has_recap_been_sent(
        self, recap_date: date, recap_type: str
    ) -> bool:
        """Has a recap of ``recap_type`` (``"daily"`` or ``"weekly"``)
        been sent for ``recap_date`` already? Used by the scheduled
        cron to skip a day that's already had its early-fire recap,
        and by the early-fire path to avoid double-sending."""
        ...

    def mark_recap_sent(
        self, recap_date: date, recap_type: str
    ) -> None:
        """Record that a recap of ``recap_type`` has been sent for
        ``recap_date``. Idempotent — calling twice for the same
        (date, type) pair is a no-op."""
        ...

    def has_taunted_today(
        self, player_id: int, kind: str, day: date
    ) -> bool:
        """Has ``player_id`` already used the ``kind`` taunt
        (``"brag"`` or ``"gripe"``) on ``day`` (LA)? Drives the
        per-day per-command cooldown so a single player can't blast
        the group with the same prompt 50 times in a row."""
        ...

    def record_taunt(
        self, player_id: int, kind: str, day: date
    ) -> None:
        """Record that ``player_id`` used the ``kind`` taunt on
        ``day``. Idempotent."""
        ...


# ---------------------------------------------------------------------------
# In-memory implementation (tests + local fallback)
# ---------------------------------------------------------------------------


@dataclass
class InMemoryRepository:
    """Simple dict-backed repo. Not thread-safe, not persistent."""

    _players: Dict[str, Player] = field(default_factory=dict)
    _next_player_id: int = 1
    _score_keys: set = field(default_factory=set)
    scores: List[Dict[str, Any]] = field(default_factory=list)
    unparsed: List[Dict[str, Any]] = field(default_factory=list)
    # (date, type) → sent. Stores a set since the only thing we ever
    # ask is "has this pair been recorded?".
    _recap_sent: set = field(default_factory=set)
    # (player_id, kind, day) → recorded. Drives the per-day
    # per-command cooldown for the brag/gripe Easter eggs.
    _taunt_log: set = field(default_factory=set)

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
        )
        self._players[existing.whatsapp_id] = updated

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

    def list_scores(
        self,
        *,
        date_from: date,
        date_to: date,
    ) -> List[ScoreRow]:
        names_by_id = {p.id: p.display_name for p in self._players.values()}
        rows: List[ScoreRow] = []
        for s in self.scores:
            if date_from <= s["puzzle_date"] <= date_to:
                rows.append(
                    ScoreRow(
                        player_id=s["player_id"],
                        player_name=names_by_id.get(s["player_id"], ""),
                        game=s["game"],
                        puzzle_no=s["puzzle_no"],
                        puzzle_date=s["puzzle_date"],
                        raw_score=s["raw_score"],
                    )
                )
        return rows

    def list_active_whatsapp_ids(
        self,
        *,
        date_from: date,
        date_to: date,
    ) -> List[str]:
        active_pids = {
            s["player_id"]
            for s in self.scores
            if date_from <= s["puzzle_date"] <= date_to
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

    def list_player_scores(self, player_id: int) -> List[ScoreRow]:
        names_by_id = {p.id: p.display_name for p in self._players.values()}
        return [
            ScoreRow(
                player_id=s["player_id"],
                player_name=names_by_id.get(s["player_id"], ""),
                game=s["game"],
                puzzle_no=s["puzzle_no"],
                puzzle_date=s["puzzle_date"],
                raw_score=s["raw_score"],
            )
            for s in self.scores
            if s["player_id"] == player_id
        ]

    def list_recent_unparsed(self, *, limit: int = 10) -> List[Dict[str, Any]]:
        return list(reversed(self.unparsed[-limit:]))

    def list_players_active_since(self, since: date) -> List[Player]:
        active_ids = {
            s["player_id"]
            for s in self.scores
            if s["puzzle_date"] >= since
        }
        # Preserve player_id order so the morning-nudge sequence is
        # deterministic in tests and predictable in logs.
        by_id = {p.id: p for p in self._players.values()}
        return [by_id[pid] for pid in sorted(active_ids) if pid in by_id]

    def has_recap_been_sent(
        self, recap_date: date, recap_type: str
    ) -> bool:
        return (recap_date, recap_type) in self._recap_sent

    def mark_recap_sent(
        self, recap_date: date, recap_type: str
    ) -> None:
        self._recap_sent.add((recap_date, recap_type))

    def has_taunted_today(
        self, player_id: int, kind: str, day: date
    ) -> bool:
        return (player_id, kind, day) in self._taunt_log

    def record_taunt(
        self, player_id: int, kind: str, day: date
    ) -> None:
        self._taunt_log.add((player_id, kind, day))


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

    def _player_select_cols(self) -> str:
        """SELECT column list for reads against ``players`` — omits
        ``notifications_enabled`` on old schemas so the query doesn't
        fail with a column-does-not-exist error."""
        base = "id, whatsapp_id, display_name"
        if self._has_notifications_column:
            base += ", notifications_enabled"
        return base

    def _row_to_player(self, row: Dict[str, Any]) -> Player:
        # ``notifications_enabled`` is a recent column; fall back to
        # ``True`` when the field is missing so old schemas still load.
        return Player(
            id=row["id"],
            whatsapp_id=row["whatsapp_id"],
            display_name=row["display_name"],
            notifications_enabled=row.get("notifications_enabled", True),
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
        (
            self._client.table("scores")
            .insert(
                {
                    "player_id": player_id,
                    "game": game,
                    "puzzle_no": puzzle_no,
                    "puzzle_date": puzzle_date.isoformat(),
                    "raw_score": raw_score,
                    "share_text": share_text,
                }
            )
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
    ) -> List[ScoreRow]:
        # PostgREST embedded join: ``players(display_name)`` inlines the
        # parent row under a ``players`` key on each returned score row.
        resp = (
            self._client.table("scores")
            .select(
                "player_id, game, puzzle_no, puzzle_date, raw_score, "
                "players(display_name)"
            )
            .gte("puzzle_date", date_from.isoformat())
            .lte("puzzle_date", date_to.isoformat())
            .execute()
        )
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
    ) -> List[str]:
        # Embed the notifications_enabled column only if it exists —
        # otherwise this SELECT would fail with "column does not
        # exist" and take every recap command down with it.
        embed_cols = "whatsapp_id"
        if self._has_notifications_column:
            embed_cols += ", notifications_enabled"
        resp = (
            self._client.table("scores")
            .select(f"player_id, players({embed_cols})")
            .gte("puzzle_date", date_from.isoformat())
            .lte("puzzle_date", date_to.isoformat())
            .execute()
        )
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

    def list_player_scores(self, player_id: int) -> List[ScoreRow]:
        resp = (
            self._client.table("scores")
            .select(
                "player_id, game, puzzle_no, puzzle_date, raw_score, "
                "players(display_name)"
            )
            .eq("player_id", player_id)
            .order("puzzle_date", desc=True)
            .execute()
        )
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

    def list_players_active_since(self, since: date) -> List[Player]:
        # Two queries on purpose: first the distinct active player_ids
        # for the date window, then the players themselves. PostgREST
        # doesn't support DISTINCT in the embed selector, so doing it
        # in Python avoids a giant deduped JSON payload.
        scores_resp = (
            self._client.table("scores")
            .select("player_id")
            .gte("puzzle_date", since.isoformat())
            .execute()
        )
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
        self, recap_date: date, recap_type: str
    ) -> bool:
        resp = (
            self._client.table("recap_log")
            .select("id")
            .eq("recap_date", recap_date.isoformat())
            .eq("recap_type", recap_type)
            .limit(1)
            .execute()
        )
        return bool(resp.data)

    def mark_recap_sent(
        self, recap_date: date, recap_type: str
    ) -> None:
        # Pre-check rather than relying on the unique constraint —
        # supabase-py surfaces conflict errors as raised exceptions
        # and we want this method to be quietly idempotent.
        if self.has_recap_been_sent(recap_date, recap_type):
            return
        (
            self._client.table("recap_log")
            .insert(
                {
                    "recap_date": recap_date.isoformat(),
                    "recap_type": recap_type,
                }
            )
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
        self, player_id: int, kind: str, day: date
    ) -> bool:
        return self.has_recap_been_sent(day, self._taunt_key(player_id, kind))

    def record_taunt(
        self, player_id: int, kind: str, day: date
    ) -> None:
        self.mark_recap_sent(day, self._taunt_key(player_id, kind))
