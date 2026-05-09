"""Shared pytest fixtures and helpers.

The group-isolation refactor introduced a mandatory ``group_id`` on
every score / recap / taunt repo call. Most existing tests were
written when the bot had a single global pool, so they construct an
``InMemoryRepository`` directly and call repo methods or
``handle_inbound`` without any group setup.

To keep that test surface intact with minimal per-test churn, this
module exposes :class:`TestRepo` — an :class:`InMemoryRepository`
subclass that:

- pre-creates a ``default`` group on construction;
- auto-assigns any new player to the default group on
  :meth:`get_or_create_player` (so :func:`handle_inbound` skips the
  onboarding gate immediately);
- defaults the ``group_id`` kwarg on every group-scoped method to the
  default group's id, so existing test-setup calls
  (``repo.insert_score(...)``, ``repo.list_scores(...)``, etc.) keep
  working unchanged.

Tests that exercise multi-group isolation explicitly should pass
``group_id`` to these methods to override the default.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.db import Group, InMemoryRepository, Player, ScoreRow

DEFAULT_GROUP_NAME = "default"


class TestRepo(InMemoryRepository):
    """In-memory repo with a pre-seeded default group + auto-onboarding."""

    # Pytest auto-collects classes whose names start with ``Test`` —
    # tell it not to, since this is a fixture class, not a test.
    __test__ = False

    default_group: Group

    def __init__(self) -> None:
        super().__init__()
        self.default_group = self.get_or_create_group(DEFAULT_GROUP_NAME)

    # Auto-onboard new players so existing tests don't need to call
    # ``set_player_group`` themselves before using ``handle_inbound``.
    def get_or_create_player(
        self, whatsapp_id: str, display_name: str
    ) -> Player:
        was_new = whatsapp_id not in self._players
        player = super().get_or_create_player(whatsapp_id, display_name)
        if was_new:
            super().set_player_group(player.id, self.default_group.id)
            player = super().get_or_create_player(whatsapp_id, display_name)
        return player

    # Default ``group_id`` on every group-scoped method to the
    # default group so existing tests that don't pass ``group_id``
    # still work. Each override forwards to the parent unchanged
    # when the caller supplies an explicit ``group_id``.
    def insert_score(
        self,
        *,
        player_id: int,
        group_id: Optional[int] = None,
        game: str,
        puzzle_no: int,
        puzzle_date: date,
        raw_score: int,
        share_text: str,
    ) -> bool:
        return super().insert_score(
            player_id=player_id,
            group_id=group_id if group_id is not None else self.default_group.id,
            game=game,
            puzzle_no=puzzle_no,
            puzzle_date=puzzle_date,
            raw_score=raw_score,
            share_text=share_text,
        )

    def list_scores(
        self,
        *,
        date_from: date,
        date_to: date,
        group_id: Optional[int] = None,
    ) -> List[ScoreRow]:
        return super().list_scores(
            date_from=date_from,
            date_to=date_to,
            group_id=group_id if group_id is not None else self.default_group.id,
        )

    def list_active_whatsapp_ids(
        self,
        *,
        date_from: date,
        date_to: date,
        group_id: Optional[int] = None,
    ) -> List[str]:
        return super().list_active_whatsapp_ids(
            date_from=date_from,
            date_to=date_to,
            group_id=group_id if group_id is not None else self.default_group.id,
        )

    def list_player_scores(
        self, player_id: int, *, group_id: Optional[int] = None
    ) -> List[ScoreRow]:
        return super().list_player_scores(
            player_id,
            group_id=group_id if group_id is not None else self.default_group.id,
        )

    def list_players_active_since(
        self, since: date, *, group_id: Optional[int] = None
    ) -> List[Player]:
        return super().list_players_active_since(
            since,
            group_id=group_id if group_id is not None else self.default_group.id,
        )

    def has_recap_been_sent(
        self, recap_date: date, recap_type: str, *, group_id: Optional[int] = None
    ) -> bool:
        return super().has_recap_been_sent(
            recap_date,
            recap_type,
            group_id=group_id if group_id is not None else self.default_group.id,
        )

    def mark_recap_sent(
        self, recap_date: date, recap_type: str, *, group_id: Optional[int] = None
    ) -> None:
        super().mark_recap_sent(
            recap_date,
            recap_type,
            group_id=group_id if group_id is not None else self.default_group.id,
        )

    def has_taunted_today(
        self,
        player_id: int,
        kind: str,
        day: date,
        *,
        group_id: Optional[int] = None,
    ) -> bool:
        return super().has_taunted_today(
            player_id,
            kind,
            day,
            group_id=group_id if group_id is not None else self.default_group.id,
        )

    def record_taunt(
        self,
        player_id: int,
        kind: str,
        day: date,
        *,
        group_id: Optional[int] = None,
    ) -> None:
        super().record_taunt(
            player_id,
            kind,
            day,
            group_id=group_id if group_id is not None else self.default_group.id,
        )

    def list_today_for_game(
        self, *, game: str, day: date, group_id: Optional[int] = None
    ) -> List[ScoreRow]:
        return super().list_today_for_game(
            game=game,
            day=day,
            group_id=group_id if group_id is not None else self.default_group.id,
        )

    def get_top_extremes_for_game(
        self,
        *,
        game: str,
        n: int = 2,
        group_id: Optional[int] = None,
    ) -> Tuple[List[ScoreRow], List[ScoreRow]]:
        return super().get_top_extremes_for_game(
            game=game,
            n=n,
            group_id=group_id if group_id is not None else self.default_group.id,
        )


def make_repo(*, group_name: str = DEFAULT_GROUP_NAME) -> Tuple[TestRepo, Group]:
    """Build a test repo + return the seeded default group."""
    repo = TestRepo()
    if group_name != DEFAULT_GROUP_NAME:
        # Caller wants a non-default group name — overwrite via the
        # in-memory bookkeeping rather than re-creating the repo.
        repo.default_group = repo.get_or_create_group(group_name)
    return repo, repo.default_group


def make_extra_group(repo: TestRepo, name: str) -> Group:
    """Create another group on the same repo. Used by isolation tests."""
    return repo.get_or_create_group(name)


@pytest.fixture
def repo_and_group() -> Tuple[TestRepo, Group]:
    """Fresh test repo + default group, one per test."""
    return make_repo()


@pytest.fixture
def repo(repo_and_group) -> TestRepo:
    return repo_and_group[0]


@pytest.fixture
def default_group(repo_and_group) -> Group:
    return repo_and_group[1]
