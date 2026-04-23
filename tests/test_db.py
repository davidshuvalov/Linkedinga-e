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


class TestInMemoryListScores:
    def test_empty_repo_returns_empty_list(self):
        repo = InMemoryRepository()
        assert (
            repo.list_scores(
                date_from=date(2026, 4, 1), date_to=date(2026, 4, 30)
            )
            == []
        )

    def test_returns_rows_with_player_name_joined(self):
        repo = InMemoryRepository()
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        repo.insert_score(
            player_id=alice.id,
            game="queens",
            puzzle_no=714,
            puzzle_date=date(2026, 4, 14),
            raw_score=10,
            share_text="Queens #714",
        )
        rows = repo.list_scores(
            date_from=date(2026, 4, 14), date_to=date(2026, 4, 14)
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
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        for d in (date(2026, 4, 13), date(2026, 4, 14), date(2026, 4, 15)):
            repo.insert_score(
                player_id=alice.id,
                game="queens",
                puzzle_no=d.day,
                puzzle_date=d,
                raw_score=10,
                share_text="...",
            )
        rows = repo.list_scores(
            date_from=date(2026, 4, 14), date_to=date(2026, 4, 14)
        )
        assert len(rows) == 1
        assert rows[0].puzzle_date == date(2026, 4, 14)

    def test_range_is_inclusive_on_both_ends(self):
        repo = InMemoryRepository()
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        for d in (date(2026, 4, 13), date(2026, 4, 14), date(2026, 4, 15)):
            repo.insert_score(
                player_id=alice.id,
                game="queens",
                puzzle_no=d.day,
                puzzle_date=d,
                raw_score=10,
                share_text="...",
            )
        rows = repo.list_scores(
            date_from=date(2026, 4, 13), date_to=date(2026, 4, 15)
        )
        assert {r.puzzle_date for r in rows} == {
            date(2026, 4, 13),
            date(2026, 4, 14),
            date(2026, 4, 15),
        }

    def test_joins_correct_name_for_each_player(self):
        repo = InMemoryRepository()
        alice = repo.get_or_create_player("whatsapp:+1", "Alice")
        bob = repo.get_or_create_player("whatsapp:+2", "Bob")
        repo.insert_score(
            player_id=alice.id,
            game="queens",
            puzzle_no=714,
            puzzle_date=date(2026, 4, 14),
            raw_score=10,
            share_text="...",
        )
        repo.insert_score(
            player_id=bob.id,
            game="queens",
            puzzle_no=714,
            puzzle_date=date(2026, 4, 14),
            raw_score=20,
            share_text="...",
        )
        rows = repo.list_scores(
            date_from=date(2026, 4, 14), date_to=date(2026, 4, 14)
        )
        by_id = {r.player_id: r.player_name for r in rows}
        assert by_id[alice.id] == "Alice"
        assert by_id[bob.id] == "Bob"


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
