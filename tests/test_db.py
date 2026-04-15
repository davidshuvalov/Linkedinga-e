"""Tests for ``app.db.InMemoryRepository.list_scores``.

The Supabase implementation is covered by manual smoke-testing against a
real project; here we lock in the interface contract against the
in-memory repo that ``app.scoring`` actually reads from in tests and CLI
demo mode.
"""

from __future__ import annotations

from datetime import date

from app.db import InMemoryRepository, ScoreRow


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
