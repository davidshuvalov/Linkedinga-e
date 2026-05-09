"""Tests for ``app.db.InMemoryRepository.list_scores``.

The Supabase implementation is covered by manual smoke-testing against a
real project; here we lock in the interface contract against the
in-memory repo that ``app.scoring`` actually reads from in tests and CLI
demo mode.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from app.db import InMemoryRepository, ScoreRow, SupabaseRepository


def _seed_group(repo: InMemoryRepository, name: str = "default") -> int:
    """Helper — every list_scores / insert_score call needs a
    group_id since the group-isolation refactor. Tests in this file
    operate on the raw :class:`InMemoryRepository` (no auto-onboarding),
    so we explicitly seed a group and pass its id through."""
    return repo.get_or_create_group(name).id


class TestInMemoryListScores:
    def test_empty_repo_returns_empty_list(self):
        repo = InMemoryRepository()
        gid = _seed_group(repo)
        assert (
            repo.list_scores(
                date_from=date(2026, 4, 1),
                date_to=date(2026, 4, 30),
                group_id=gid,
            )
            == []
        )

    def test_returns_rows_with_player_name_joined(self):
        repo = InMemoryRepository()
        gid = _seed_group(repo)
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.insert_score(
            player_id=alice.id,
            group_id=gid,
            game="queens",
            puzzle_no=714,
            puzzle_date=date(2026, 4, 14),
            raw_score=10,
            share_text="Queens #714",
        )
        rows = repo.list_scores(
            date_from=date(2026, 4, 14),
            date_to=date(2026, 4, 14),
            group_id=gid,
        )
        assert len(rows) == 1
        assert rows[0] == ScoreRow(
            player_id=alice.id,
            player_name="Alice",
            game="queens",
            puzzle_no=714,
            puzzle_date=date(2026, 4, 14),
            raw_score=10,
        )

    def test_excludes_rows_outside_range(self):
        repo = InMemoryRepository()
        gid = _seed_group(repo)
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        for d in (date(2026, 4, 13), date(2026, 4, 14), date(2026, 4, 15)):
            repo.insert_score(
                player_id=alice.id,
                group_id=gid,
                game="queens",
                puzzle_no=d.day,
                puzzle_date=d,
                raw_score=10,
                share_text="...",
            )
        rows = repo.list_scores(
            date_from=date(2026, 4, 14),
            date_to=date(2026, 4, 14),
            group_id=gid,
        )
        assert len(rows) == 1
        assert rows[0].puzzle_date == date(2026, 4, 14)

    def test_range_is_inclusive_on_both_ends(self):
        repo = InMemoryRepository()
        gid = _seed_group(repo)
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        for d in (date(2026, 4, 13), date(2026, 4, 14), date(2026, 4, 15)):
            repo.insert_score(
                player_id=alice.id,
                group_id=gid,
                game="queens",
                puzzle_no=d.day,
                puzzle_date=d,
                raw_score=10,
                share_text="...",
            )
        rows = repo.list_scores(
            date_from=date(2026, 4, 13),
            date_to=date(2026, 4, 15),
            group_id=gid,
        )
        assert {r.puzzle_date for r in rows} == {
            date(2026, 4, 13),
            date(2026, 4, 14),
            date(2026, 4, 15),
        }

    def test_joins_correct_name_for_each_player(self):
        repo = InMemoryRepository()
        gid = _seed_group(repo)
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        bob = repo.get_or_create_player("whatsapp:+2", "Bob")
        repo.insert_score(
            player_id=alice.id,
            group_id=gid,
            game="queens",
            puzzle_no=714,
            puzzle_date=date(2026, 4, 14),
            raw_score=10,
            share_text="...",
        )
        repo.insert_score(
            player_id=bob.id,
            group_id=gid,
            game="queens",
            puzzle_no=714,
            puzzle_date=date(2026, 4, 14),
            raw_score=20,
            share_text="...",
        )
        rows = repo.list_scores(
            date_from=date(2026, 4, 14),
            date_to=date(2026, 4, 14),
            group_id=gid,
        )
        by_id = {r.player_id: r.player_name for r in rows}
        assert by_id[alice.id] == "Alice"
        assert by_id[bob.id] == "Bob"


class TestGroupMethods:
    """The :class:`Group` dataclass + the new group-management methods
    on :class:`InMemoryRepository`."""

    def test_get_or_create_group_is_case_insensitive(self):
        repo = InMemoryRepository()
        first = repo.get_or_create_group("Crew")
        second = repo.get_or_create_group("CREW")
        assert first.id == second.id
        assert first.name == "Crew"  # original casing preserved
        assert first.name_lower == "crew"

    def test_find_group_by_name_returns_none_when_missing(self):
        repo = InMemoryRepository()
        assert repo.find_group_by_name("ghost") is None

    def test_set_player_group_does_not_move_existing_scores(self):
        repo = InMemoryRepository()
        a = repo.get_or_create_group("ACrew")
        b = repo.get_or_create_group("BCrew")
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.set_player_group(alice.id, a.id)
        repo.insert_score(
            player_id=alice.id, group_id=a.id, game="queens",
            puzzle_no=714, puzzle_date=date(2026, 4, 14),
            raw_score=10, share_text="x",
        )
        # Move Alice to BCrew. Old score must stay in ACrew.
        repo.set_player_group(alice.id, b.id)
        a_scores = repo.list_scores(
            date_from=date(2026, 4, 1),
            date_to=date(2026, 4, 30),
            group_id=a.id,
        )
        b_scores = repo.list_scores(
            date_from=date(2026, 4, 1),
            date_to=date(2026, 4, 30),
            group_id=b.id,
        )
        assert len(a_scores) == 1
        assert len(b_scores) == 0

    def test_list_groups_returns_all_groups(self):
        repo = InMemoryRepository()
        repo.get_or_create_group("ACrew")
        repo.get_or_create_group("BCrew")
        groups = repo.list_groups()
        assert len(groups) == 2
        names = {g.name for g in groups}
        assert names == {"ACrew", "BCrew"}

    def test_recap_log_keyed_by_group(self):
        repo = InMemoryRepository()
        a = repo.get_or_create_group("ACrew")
        b = repo.get_or_create_group("BCrew")
        # Mark daily for ACrew on apr 14; BCrew should still report not sent.
        repo.mark_recap_sent(date(2026, 4, 14), "daily", group_id=a.id)
        assert repo.has_recap_been_sent(
            date(2026, 4, 14), "daily", group_id=a.id
        )
        assert not repo.has_recap_been_sent(
            date(2026, 4, 14), "daily", group_id=b.id
        )

    def test_taunt_cooldown_keyed_by_group(self):
        repo = InMemoryRepository()
        a = repo.get_or_create_group("ACrew")
        b = repo.get_or_create_group("BCrew")
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.record_taunt(alice.id, "brag", date(2026, 4, 14), group_id=a.id)
        # Same player's brag in a different group is a separate token.
        assert repo.has_taunted_today(
            alice.id, "brag", date(2026, 4, 14), group_id=a.id
        )
        assert not repo.has_taunted_today(
            alice.id, "brag", date(2026, 4, 14), group_id=b.id
        )


class TestSupabaseSchemaDetection:
    """``SupabaseRepository`` probes for the ``notifications_enabled``
    column once on construction and gracefully omits it from later
    SELECT lists if the migration hasn't been applied yet. Keeps the
    bot from erroring on every inbound message against a fresh DB."""

    def _mock_client(self, column_present: bool):
        """Fake ``supabase-py`` client. The initial canary probe
        either succeeds (column present) or raises (column missing)."""
        client = MagicMock()
        if column_present:
            client.table.return_value.select.return_value.limit.return_value.execute.return_value = (
                MagicMock(data=[])
            )
        else:
            client.table.return_value.select.return_value.limit.return_value.execute.side_effect = (
                Exception("column players.notifications_enabled does not exist")
            )
        return client

    def test_detects_column_present(self):
        client = self._mock_client(column_present=True)
        repo = SupabaseRepository(client)
        assert repo._has_notifications_column is True
        assert "notifications_enabled" in repo._player_select_cols()

    def test_survives_missing_column(self):
        client = self._mock_client(column_present=False)
        # Construction must not raise even though the probe failed.
        repo = SupabaseRepository(client)
        assert repo._has_notifications_column is False
        # Player select omits the missing column so downstream
        # queries don't crash.
        assert "notifications_enabled" not in repo._player_select_cols()

    def test_set_notifications_enabled_raises_when_column_missing(self):
        client = self._mock_client(column_present=False)
        repo = SupabaseRepository(client)
        import pytest

        with pytest.raises(RuntimeError, match="notifications_enabled"):
            repo.set_notifications_enabled(1, False)
