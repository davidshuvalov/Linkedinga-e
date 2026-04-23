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
