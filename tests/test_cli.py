"""Smoke tests for ``app.cli``.

The heavy lifting (scoring math, output format) is covered by
``test_scoring.py`` and ``test_scheduler.py``. These tests just verify the
argparse plumbing and the ``--demo`` seed path end-to-end.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.cli import _seed_demo, main
from app.db import InMemoryRepository


class TestSeedDemo:
    def test_seeds_players_and_scores(self):
        repo = InMemoryRepository()
        anchor = date(2026, 4, 14)
        _seed_demo(repo, today=anchor)
        # Multiple players, multiple games, at least two puzzle_dates
        assert len(repo._players) >= 4
        assert len(repo.scores) > 0
        dates = {s["puzzle_date"] for s in repo.scores}
        assert anchor in dates
        assert anchor - timedelta(days=1) in dates


class TestCliMain:
    def test_recap_demo_prints_daily_header(self, capsys):
        exit_code = main(["recap", "--demo"])
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Daily recap" in out

    def test_wrap_demo_prints_weekly_header(self, capsys):
        exit_code = main(["wrap", "--demo"])
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Weekly wrap" in out

    def test_recap_with_explicit_date_demo_mode_is_empty_for_unseeded_date(
        self, capsys
    ):
        # A date in the distant past won't have any seeded rows, so the
        # recap should render the no-scores placeholder.
        exit_code = main(["recap", "--demo", "--date", "2000-01-01"])
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "No scores yet" in out

    def test_wrap_with_explicit_week_of_demo(self, capsys):
        exit_code = main(["wrap", "--demo", "--week-of", "2026-04-15"])
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Weekly wrap" in out
        assert "Mon 13 Apr" in out

    def test_recap_demo_contains_seeded_games(self, capsys):
        today = date.today()
        # Using --demo seeds data at date.today(), so the default recap
        # date should include several of the seeded games.
        main(["recap", "--demo"])
        out = capsys.readouterr().out
        assert "Daily recap" in out
        assert today.strftime("%a %d %b %Y") in out
        # We know the seed always has Queens and Mini Sudoku on "today"
        assert "Queens #714" in out
        assert "Mini Sudoku #246" in out
