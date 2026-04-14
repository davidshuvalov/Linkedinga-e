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


class Repository(Protocol):
    """Interface used by the webhook handler."""

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

    def get_or_create_player(
        self, whatsapp_id: str, display_name: str
    ) -> Player:
        resp = (
            self._client.table("players")
            .select("id, whatsapp_id, display_name")
            .eq("whatsapp_id", whatsapp_id)
            .limit(1)
            .execute()
        )
        if resp.data:
            row = resp.data[0]
            return Player(
                id=row["id"],
                whatsapp_id=row["whatsapp_id"],
                display_name=row["display_name"],
            )
        inserted = (
            self._client.table("players")
            .insert(
                {"whatsapp_id": whatsapp_id, "display_name": display_name}
            )
            .execute()
        )
        row = inserted.data[0]
        return Player(
            id=row["id"],
            whatsapp_id=row["whatsapp_id"],
            display_name=row["display_name"],
        )

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
