"""Teams — named subsets of a group whose members' points are added.

Three layers get covered here:

- :func:`app.scoring.team_standings`, the pure fold from a player
  leaderboard to team totals;
- the repository methods backing it (against ``InMemoryRepository``,
  including the one-team-per-player-per-group invariant);
- the ``team`` / ``teams`` command surface in
  :func:`app.webhook.handle_inbound`, plus the block the weekly wrap
  appends once a group has teams.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.db import InMemoryRepository, ScoreRow
from app.jobs import render_daily, render_wrap
from app.puzzles import la_date
from app.scoring import PlayerWeeklyStats, team_standings, weekly_leaderboard
from app.webhook import handle_inbound

from .conftest import make_extra_group

SYD = ZoneInfo("Australia/Sydney")

# Noon Sydney on Thu 6 Aug 2026 is Wed 5 Aug in LA — the day the
# no-peek gate measures "today" against.
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=SYD)
LA_TODAY = la_date(NOW)
MONDAY = date(2026, 8, 3)


def _settings(games=("queens", "zip")) -> Settings:
    return Settings(
        twilio_account_sid="",
        twilio_auth_token="",
        twilio_whatsapp_from="",
        twilio_recap_to="",
        twilio_status_callback_url="",
        supabase_url="",
        supabase_key="",
        timezone_name="Australia/Sydney",
        enabled_games=frozenset(games),
    )


def _stats(player_id: int, name: str, points: float, submissions: int = 0):
    return PlayerWeeklyStats(
        player_id=player_id,
        player_name=name,
        total_points=points,
        distinct_games=1,
        days_played=1,
        submissions=submissions,
    )


def _onboard(repo, people, settings):
    """Run each person through handle_inbound once so they exist as
    players in the default group, then return ``{name: player}``."""
    out = {}
    for whatsapp_id, name in people:
        handle_inbound(
            repo, from_=whatsapp_id, body="hi", profile_name=name,
            now=NOW, settings=settings,
        )
        out[name] = repo.get_or_create_player(whatsapp_id, name)
    return out


def _seed_week(repo, players, *, days=(MONDAY,), games=("queens", "zip")):
    """Give every player a score in every game on every day, spaced so
    the finishing order matches the order of ``players``."""
    for offset, day in enumerate(days):
        for rank, player in enumerate(players):
            for game in games:
                repo.insert_score(
                    player_id=player.id,
                    game=game,
                    puzzle_no=700 + offset,
                    puzzle_date=day,
                    raw_score=60 + rank * 11,
                    share_text="x",
                )


def _send(repo, settings, body, *, whatsapp_id="whatsapp:+1", name="Alice"):
    return handle_inbound(
        repo, from_=whatsapp_id, body=body, profile_name=name,
        now=NOW, settings=settings,
    )


# ---------------------------------------------------------------------------
# scoring.team_standings
# ---------------------------------------------------------------------------


class TestTeamStandings:
    def test_adds_member_points(self):
        lb = [_stats(1, "Alice", 10.0), _stats(2, "Bob", 6.0)]
        standings = team_standings(lb, {1: 100, 2: 100}, {100: "Reds"})
        assert len(standings) == 1
        assert standings[0].total_points == 16.0
        assert standings[0].member_count == 2

    def test_sorted_by_points_desc(self):
        lb = [_stats(1, "Alice", 10.0), _stats(2, "Bob", 6.0), _stats(3, "Carol", 9.0)]
        standings = team_standings(
            lb, {1: 100, 2: 200, 3: 200}, {100: "Reds", 200: "Blues"}
        )
        # Blues 6 + 9 = 15 beats Reds' 10 despite Alice being top player.
        assert [t.team_name for t in standings] == ["Blues", "Reds"]
        assert [t.total_points for t in standings] == [15.0, 10.0]

    def test_ties_break_on_team_id(self):
        lb = [_stats(1, "Alice", 5.0), _stats(2, "Bob", 5.0)]
        standings = team_standings(
            lb, {1: 200, 2: 100}, {100: "Reds", 200: "Blues"}
        )
        assert [t.team_id for t in standings] == [100, 200]

    def test_team_with_no_scores_still_listed(self):
        lb = [_stats(1, "Alice", 10.0)]
        standings = team_standings(
            lb, {1: 100, 2: 200}, {100: "Reds", 200: "Blues"}
        )
        blues = next(t for t in standings if t.team_name == "Blues")
        assert blues.total_points == 0.0
        # Bob is on the roster even though he scored nothing this period.
        assert blues.member_count == 1
        assert blues.scoring_members == ()

    def test_empty_team_listed_with_zero(self):
        standings = team_standings([], {}, {100: "Reds"})
        assert standings[0].total_points == 0.0
        assert standings[0].member_count == 0
        assert standings[0].average_points == 0.0

    def test_unteamed_players_excluded(self):
        lb = [_stats(1, "Alice", 10.0), _stats(2, "Bob", 6.0)]
        standings = team_standings(lb, {1: 100}, {100: "Reds"})
        assert standings[0].total_points == 10.0
        assert [p.player_name for p in standings[0].scoring_members] == ["Alice"]

    def test_membership_to_unknown_team_ignored(self):
        lb = [_stats(1, "Alice", 10.0), _stats(2, "Bob", 6.0)]
        # Bob's team was deleted between the two repo reads.
        standings = team_standings(lb, {1: 100, 2: 999}, {100: "Reds"})
        assert len(standings) == 1
        assert standings[0].total_points == 10.0
        assert standings[0].member_count == 1

    def test_average_is_per_roster_member_not_per_scorer(self):
        lb = [_stats(1, "Alice", 12.0)]
        standings = team_standings(lb, {1: 100, 2: 100}, {100: "Reds"})
        # 12 points over a roster of 2, not over the 1 who played.
        assert standings[0].average_points == 6.0

    def test_submissions_sum(self):
        lb = [
            _stats(1, "Alice", 10.0, submissions=4),
            _stats(2, "Bob", 6.0, submissions=3),
        ]
        standings = team_standings(lb, {1: 100, 2: 100}, {100: "Reds"})
        assert standings[0].submissions == 7

    def test_scoring_members_sorted_by_points(self):
        lb = [_stats(1, "Alice", 4.0), _stats(2, "Bob", 9.0)]
        standings = team_standings(lb, {1: 100, 2: 100}, {100: "Reds"})
        assert [p.player_name for p in standings[0].scoring_members] == ["Bob", "Alice"]

    def test_matches_weekly_leaderboard_totals(self):
        """The team total is exactly the sum of the same players'
        rows on the player leaderboard — no separate scoring path."""
        rows = [
            ScoreRow(1, "Alice", "queens", 700, MONDAY, 60),
            ScoreRow(2, "Bob", "queens", 700, MONDAY, 71),
            ScoreRow(3, "Carol", "queens", 700, MONDAY, 82),
        ]
        lb = weekly_leaderboard(rows)
        by_id = {p.player_id: p.total_points for p in lb}
        standings = team_standings(lb, {1: 100, 2: 100}, {100: "Reds"})
        assert standings[0].total_points == pytest.approx(by_id[1] + by_id[2])


# ---------------------------------------------------------------------------
# repository layer
# ---------------------------------------------------------------------------


class TestTeamRepository:
    def test_create_is_idempotent_per_group(self, repo, default_group):
        first = repo.create_team(group_id=default_group.id, name="Reds")
        second = repo.create_team(group_id=default_group.id, name="reds")
        assert first.id == second.id
        assert first.name == "Reds"  # original casing preserved

    def test_same_name_in_two_groups_is_two_teams(self, repo, default_group):
        other = make_extra_group(repo, "other")
        a = repo.create_team(group_id=default_group.id, name="Reds")
        b = repo.create_team(group_id=other.id, name="Reds")
        assert a.id != b.id
        assert [t.id for t in repo.list_teams(default_group.id)] == [a.id]
        assert [t.id for t in repo.list_teams(other.id)] == [b.id]

    def test_find_by_name_is_case_insensitive_and_group_scoped(
        self, repo, default_group
    ):
        other = make_extra_group(repo, "other")
        team = repo.create_team(group_id=default_group.id, name="Reds")
        assert repo.find_team_by_name(
            group_id=default_group.id, name="REDS"
        ).id == team.id
        assert repo.find_team_by_name(group_id=other.id, name="Reds") is None

    def test_player_belongs_to_one_team_per_group(self, repo, default_group):
        reds = repo.create_team(group_id=default_group.id, name="Reds")
        blues = repo.create_team(group_id=default_group.id, name="Blues")
        repo.add_team_member(
            team_id=reds.id, group_id=default_group.id, player_id=7
        )
        repo.add_team_member(
            team_id=blues.id, group_id=default_group.id, player_id=7
        )
        assert repo.list_team_memberships(default_group.id) == {7: blues.id}

    def test_memberships_are_group_scoped(self, repo, default_group):
        other = make_extra_group(repo, "other")
        reds = repo.create_team(group_id=default_group.id, name="Reds")
        greens = repo.create_team(group_id=other.id, name="Greens")
        repo.add_team_member(
            team_id=reds.id, group_id=default_group.id, player_id=7
        )
        repo.add_team_member(team_id=greens.id, group_id=other.id, player_id=7)
        assert repo.list_team_memberships(default_group.id) == {7: reds.id}
        assert repo.list_team_memberships(other.id) == {7: greens.id}

    def test_remove_member_reports_whether_anything_changed(
        self, repo, default_group
    ):
        reds = repo.create_team(group_id=default_group.id, name="Reds")
        repo.add_team_member(
            team_id=reds.id, group_id=default_group.id, player_id=7
        )
        assert repo.remove_team_member(group_id=default_group.id, player_id=7) is True
        assert repo.remove_team_member(group_id=default_group.id, player_id=7) is False

    def test_delete_team_drops_its_memberships_only(self, repo, default_group):
        reds = repo.create_team(group_id=default_group.id, name="Reds")
        blues = repo.create_team(group_id=default_group.id, name="Blues")
        repo.add_team_member(
            team_id=reds.id, group_id=default_group.id, player_id=7
        )
        repo.add_team_member(
            team_id=blues.id, group_id=default_group.id, player_id=8
        )
        repo.delete_team(reds.id)
        assert [t.id for t in repo.list_teams(default_group.id)] == [blues.id]
        assert repo.list_team_memberships(default_group.id) == {8: blues.id}

    def test_delete_unknown_team_is_a_noop(self, repo, default_group):
        repo.delete_team(4242)
        assert repo.list_teams(default_group.id) == []


# ---------------------------------------------------------------------------
# team commands
# ---------------------------------------------------------------------------


PEOPLE = [
    ("whatsapp:+1", "Alice"),
    ("whatsapp:+2", "Bob"),
    ("whatsapp:+3", "Carol"),
    ("whatsapp:+4", "Dave"),
]


@pytest.fixture
def crew(repo):
    """Four onboarded players with scores on Monday *and* today, plus
    the settings the team commands need.

    Today matters: the team leaderboard is behind the same no-peek
    gate as the player one, so a crew that hasn't played today can't
    read its own standings. :class:`TestTeamLeaderboard` covers that
    case explicitly with its own repo.
    """
    settings = _settings()
    players = _onboard(repo, PEOPLE, settings)
    _seed_week(
        repo,
        [players[n] for n in ("Alice", "Bob", "Carol", "Dave")],
        days=(MONDAY, LA_TODAY),
    )
    return repo, settings, players


class TestTeamCreation:
    def test_bare_teams_shows_usage_when_none_exist(self, crew):
        repo, settings, _ = crew
        reply = _send(repo, settings, "teams")
        assert "No teams in this group yet." in reply
        assert "team <name>: <player>, <player>" in reply

    def test_create_with_colon(self, crew):
        repo, settings, players = crew
        reply = _send(repo, settings, "team Reds: Alice, Bob")
        assert "Created team `Reds`" in reply
        assert "Alice, Bob" in reply
        memberships = repo.list_team_memberships(repo.default_group.id)
        team = repo.find_team_by_name(
            group_id=repo.default_group.id, name="reds"
        )
        assert memberships == {
            players["Alice"].id: team.id,
            players["Bob"].id: team.id,
        }

    def test_create_without_colon(self, crew):
        repo, settings, _ = crew
        reply = _send(repo, settings, "team Reds Alice Bob")
        assert "Created team `Reds`" in reply
        assert len(repo.list_team_memberships(repo.default_group.id)) == 2

    def test_and_separates_members(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice and Bob and Carol")
        assert len(repo.list_team_memberships(repo.default_group.id)) == 3

    def test_me_resolves_to_sender(self, crew):
        repo, settings, players = crew
        _send(repo, settings, "team Reds: me, Bob", whatsapp_id="whatsapp:+1",
              name="Alice")
        assert players["Alice"].id in repo.list_team_memberships(
            repo.default_group.id
        )

    def test_names_are_case_insensitive(self, crew):
        repo, settings, _ = crew
        reply = _send(repo, settings, "team Reds: alice, BOB")
        assert "Created team `Reds`" in reply
        assert len(repo.list_team_memberships(repo.default_group.id)) == 2

    def test_unique_prefix_resolves(self, crew):
        repo, settings, players = crew
        _send(repo, settings, "team Reds: Ali")
        assert repo.list_team_memberships(repo.default_group.id) == {
            players["Alice"].id: repo.find_team_by_name(
                group_id=repo.default_group.id, name="Reds"
            ).id
        }

    def test_duplicate_names_deduped(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, alice, Bob")
        assert len(repo.list_team_memberships(repo.default_group.id)) == 2

    def test_unknown_player_rejected_and_team_not_created(self, crew):
        repo, settings, _ = crew
        reply = _send(repo, settings, "team Reds: Alice, Zaphod")
        assert "Couldn't find player: Zaphod" in reply
        assert "Alice, Bob, Carol, Dave" in reply
        # Nothing is written when any name fails to resolve.
        assert repo.list_teams(repo.default_group.id) == []

    def test_empty_roster_prompts_for_players(self, crew):
        repo, settings, _ = crew
        reply = _send(repo, settings, "team Reds:")
        assert "Who's on `Reds`?" in reply
        assert repo.list_teams(repo.default_group.id) == []

    def test_reserved_name_rejected(self, crew):
        repo, settings, _ = crew
        reply = _send(repo, settings, "team delete: Alice")
        assert "team command" in reply
        assert repo.list_teams(repo.default_group.id) == []

    def test_overlong_name_rejected(self, crew):
        repo, settings, _ = crew
        reply = _send(repo, settings, f"team {'R' * 31}: Alice")
        assert "Team name too long" in reply
        assert repo.list_teams(repo.default_group.id) == []

    def test_repeat_create_adds_to_existing_team(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice")
        reply = _send(repo, settings, "team Reds: Bob")
        assert "Added to `Reds`" in reply
        assert len(repo.list_teams(repo.default_group.id)) == 1
        assert len(repo.list_team_memberships(repo.default_group.id)) == 2

    def test_already_on_team_is_reported_not_duplicated(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        reply = _send(repo, settings, "team Reds: Alice")
        assert reply.startswith("Alice is already on `Reds`.")
        assert len(repo.list_team_memberships(repo.default_group.id)) == 2

    def test_partial_repeat_reports_both_outcomes(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice")
        reply = _send(repo, settings, "team Reds: Alice, Bob")
        assert "Added to `Reds`:" in reply
        assert "  Bob" in reply
        assert "Already there: Alice" in reply


class TestTeamMembership:
    def test_add_requires_an_existing_team(self, crew):
        repo, settings, _ = crew
        reply = _send(repo, settings, "team add Reds: Alice")
        assert "No team called `Reds`" in reply
        assert repo.list_teams(repo.default_group.id) == []

    def test_add_to_existing_team(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice")
        reply = _send(repo, settings, "team add Reds: Bob, Carol")
        assert "Added to `Reds`" in reply
        assert len(repo.list_team_memberships(repo.default_group.id)) == 3

    def test_adding_to_a_second_team_moves_the_player(self, crew):
        repo, settings, players = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        _send(repo, settings, "team Blues: Carol")
        reply = _send(repo, settings, "team add Blues: Alice")
        assert "Moved over: Alice (from Reds)" in reply
        blues = repo.find_team_by_name(
            group_id=repo.default_group.id, name="Blues"
        )
        memberships = repo.list_team_memberships(repo.default_group.id)
        assert memberships[players["Alice"].id] == blues.id
        # Alice is on exactly one team, not two.
        assert list(memberships.values()).count(blues.id) == 2

    def test_remove_takes_player_off_their_team(self, crew):
        repo, settings, players = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        reply = _send(repo, settings, "team remove Alice")
        assert "Removed from their team: Alice" in reply
        assert players["Alice"].id not in repo.list_team_memberships(
            repo.default_group.id
        )

    def test_remove_keeps_the_players_scores(self, crew):
        repo, settings, players = crew
        before = len(repo.list_player_scores(players["Alice"].id))
        _send(repo, settings, "team Reds: Alice")
        _send(repo, settings, "team remove Alice")
        assert len(repo.list_player_scores(players["Alice"].id)) == before

    def test_remove_player_with_no_team(self, crew):
        repo, settings, _ = crew
        reply = _send(repo, settings, "team remove Alice")
        assert "wasn't on a team" in reply

    def test_remove_unknown_player(self, crew):
        repo, settings, _ = crew
        reply = _send(repo, settings, "team remove Zaphod")
        assert "Couldn't find player: Zaphod" in reply

    def test_delete_disbands_the_team(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        reply = _send(repo, settings, "team delete Reds")
        assert "Disbanded `Reds` (2 players freed up)" in reply
        assert repo.list_teams(repo.default_group.id) == []
        assert repo.list_team_memberships(repo.default_group.id) == {}

    def test_delete_unknown_team(self, crew):
        repo, settings, _ = crew
        reply = _send(repo, settings, "team delete Reds")
        assert "No team called `Reds`" in reply

    def test_disband_is_an_alias_for_delete(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice")
        _send(repo, settings, "team disband Reds")
        assert repo.list_teams(repo.default_group.id) == []


class TestTeamListing:
    def test_teams_lists_rosters_and_stragglers(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        reply = _send(repo, settings, "teams")
        assert "Teams (1):" in reply
        assert "Reds: Alice, Bob" in reply
        assert "Not on a team: Carol, Dave" in reply

    def test_named_team_shows_its_roster(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        reply = _send(repo, settings, "team Reds")
        assert reply.startswith("Reds (2):")
        assert "- Alice" in reply
        assert "- Bob" in reply


class TestTeamLeaderboard:
    def test_points_are_added_across_members(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        _send(repo, settings, "team Blues: Carol, Dave")
        reply = _send(repo, settings, "team leaderboard")

        lb = {
            p.player_name: p.total_points
            for p in weekly_leaderboard(
                repo.list_scores(date_from=MONDAY, date_to=LA_TODAY)
            )
        }
        reds_total = lb["Alice"] + lb["Bob"]
        assert "Team standings — week of Mon 03 Aug 2026:" in reply
        assert f"1. Reds — {reds_total:g} pts (2 players" in reply
        assert "Alice" in reply and "Bob" in reply

    def test_unteamed_players_are_listed_separately(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        reply = _send(repo, settings, "team leaderboard")
        assert "Not on a team:" in reply
        assert "Carol" in reply

    def test_standings_alias(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice")
        assert "Team standings" in _send(repo, settings, "team standings")

    def test_game_filter(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        reply = _send(repo, settings, "team leaderboard queens")
        assert "Team standings (Queens)" in reply

    def test_month_scope(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice")
        reply = _send(repo, settings, "team month")
        assert "Team standings — August 2026:" in reply

    def test_unknown_scope_word(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice")
        reply = _send(repo, settings, "team leaderboard bananas")
        assert "Don't know `bananas`" in reply

    def test_leaderboard_without_teams_points_at_usage(self, crew):
        repo, settings, _ = crew
        reply = _send(repo, settings, "team leaderboard")
        assert "No teams in this group yet." in reply

    def test_team_with_no_scores_still_appears(self, crew):
        repo, settings, _ = crew
        # Erin joins the group but has never submitted anything.
        handle_inbound(
            repo, from_="whatsapp:+9", body="hi", profile_name="Erin",
            now=NOW, settings=settings,
        )
        _send(repo, settings, "team Reds: Alice")
        _send(repo, settings, "team Solos: Erin")
        reply = _send(repo, settings, "team leaderboard")
        assert "Solos — 0 pts (1 player, 0 avg)" in reply
        assert "Erin 0" in reply

    def test_no_peek_gate_blocks_a_sender_who_hasnt_played_today(self, repo):
        settings = _settings()
        players = _onboard(repo, PEOPLE, settings)
        _seed_week(repo, [players["Alice"], players["Bob"]])
        # Bob plays today's round; Alice hasn't.
        for game in ("queens", "zip"):
            repo.insert_score(
                player_id=players["Bob"].id, game=game, puzzle_no=705,
                puzzle_date=LA_TODAY, raw_score=30, share_text="x",
            )
        _send(repo, settings, "team Reds: Alice, Bob")

        blocked = _send(repo, settings, "team leaderboard")
        assert "No peeking" in blocked

        allowed = _send(
            repo, settings, "team leaderboard",
            whatsapp_id="whatsapp:+2", name="Bob",
        )
        assert "Team standings" in allowed

    def test_teams_listing_is_not_gated(self, repo):
        """The roster view carries no scores, so it stays readable even
        when the no-peek gate is closed."""
        settings = _settings()
        _onboard(repo, PEOPLE, settings)
        _send(repo, settings, "team Reds: Alice, Bob")
        assert "Reds: Alice, Bob" in _send(repo, settings, "teams")


class TestTeamIsolation:
    def test_teams_do_not_leak_across_groups(self, repo):
        settings = _settings()
        _onboard(repo, PEOPLE, settings)
        _send(repo, settings, "team Reds: Alice, Bob")

        # Dave moves to another group and looks for the team there.
        _send(repo, settings, "group other", whatsapp_id="whatsapp:+4",
              name="Dave")
        reply = _send(repo, settings, "teams", whatsapp_id="whatsapp:+4",
                      name="Dave")
        assert "No teams in this group yet." in reply

    def test_team_commands_need_a_group_first(self):
        """A raw repo doesn't auto-onboard, so the sender has no
        group — team commands hit the onboarding gate like every
        other command."""
        raw = InMemoryRepository()
        reply = handle_inbound(
            raw, from_="whatsapp:+99", body="team Reds: Alice",
            profile_name="Newbie", now=NOW, settings=_settings(),
        )
        assert "group <name>" in reply
        assert "Created team" not in reply


# ---------------------------------------------------------------------------
# weekly wrap integration
# ---------------------------------------------------------------------------


def _block_order(body: str, *headings: str) -> list:
    """Index of each heading in ``body``, for asserting block order."""
    return [body.index(h) for h in headings]


class TestWrapTeamBlock:
    def test_wrap_gains_a_team_block(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        body, _targets = render_wrap(
            repo, settings, LA_TODAY, group_id=repo.default_group.id, now=NOW
        )
        assert "Team standings:" in body
        assert "1. Reds —" in body

    def test_teams_sit_above_the_individual_board(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        body, _targets = render_wrap(
            repo, settings, LA_TODAY, group_id=repo.default_group.id, now=NOW
        )
        teams_at, players_at = _block_order(
            body, "Team standings:", "Week totals:"
        )
        assert teams_at < players_at

    def test_wrap_keeps_the_roomy_per_game_layout(self):
        """The wrap is a once-a-week read, so it deliberately keeps the
        one-line-per-player per-game block that the daily recap folds.

        Goes through the formatter directly: the wrap's per-game block
        covers the week's *final* day, which the shared fixture (Mon +
        today) doesn't reach.
        """
        from app.scheduler import weekly_wrap

        sunday = date(2026, 8, 9)
        rows = [
            ScoreRow(1, "Alice", "queens", 706, sunday, 60),
            ScoreRow(2, "Bob", "queens", 706, sunday, 71),
        ]
        body = weekly_wrap(MONDAY, sunday, rows, frozenset({"queens"}))
        assert "Queens #706" in body
        # One line per player, each carrying the "pts" unit...
        assert "  Alice — 1:00 (5 pts)" in body
        assert "  Bob — 1:11 (4 pts)" in body
        # ...and no folded "a · b" run anywhere above the totals.
        assert " · " not in body.split("Week totals:")[0]

    def test_wrap_unchanged_without_teams(self, crew):
        repo, settings, _ = crew
        body, _targets = render_wrap(
            repo, settings, LA_TODAY, group_id=repo.default_group.id, now=NOW
        )
        assert "Team standings" not in body

    def test_wrap_survives_a_failing_teams_backend(self, crew, monkeypatch):
        """A pre-migration schema raises on the teams table. The wrap
        is more important than the block, so it degrades to no block."""
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice")

        def boom(_group_id):
            raise RuntimeError("teams table missing")

        monkeypatch.setattr(repo, "list_teams", boom)
        body, _targets = render_wrap(
            repo, settings, LA_TODAY, group_id=repo.default_group.id, now=NOW
        )
        assert "Week totals:" in body
        assert "Team standings" not in body


class TestRecapTeamBlock:
    """Teams in the scheduled / on-demand daily recap."""

    def test_recap_gains_a_team_block(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        body, _targets = render_daily(
            repo, settings, LA_TODAY, group_id=repo.default_group.id, now=NOW
        )
        assert "Team standings (week):" in body
        assert "1. Reds —" in body

    def test_teams_sit_above_the_individual_board(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        body, _targets = render_daily(
            repo, settings, LA_TODAY, group_id=repo.default_group.id, now=NOW
        )
        teams_at, players_at = _block_order(
            body, "Team standings (week):", "Week so far:"
        )
        assert teams_at < players_at

    def test_recap_unchanged_without_teams(self, crew):
        repo, settings, _ = crew
        body, _targets = render_daily(
            repo, settings, LA_TODAY, group_id=repo.default_group.id, now=NOW
        )
        assert "Team standings" not in body

    def test_recap_survives_a_failing_teams_backend(self, crew, monkeypatch):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice")

        def boom(_group_id):
            raise RuntimeError("teams table missing")

        monkeypatch.setattr(repo, "list_teams", boom)
        body, _targets = render_daily(
            repo, settings, LA_TODAY, group_id=repo.default_group.id, now=NOW
        )
        assert "Week so far:" in body
        assert "Team standings" not in body

    def test_past_day_recap_excludes_later_scores(self, crew):
        """A past-day recap's team totals must match the individual
        board it sits above — both are "as of" that day, so scores
        submitted after it can't leak in."""
        repo, settings, players = crew
        _send(repo, settings, "team Reds: Alice")
        body, _targets = render_daily(
            repo, settings, MONDAY, group_id=repo.default_group.id, now=NOW
        )
        monday_only = [
            p.total_points
            for p in weekly_leaderboard(
                repo.list_scores(date_from=MONDAY, date_to=MONDAY)
            )
            if p.player_id == players["Alice"].id
        ][0]
        assert f"1. Reds — {monday_only:g} pts" in body


class TestLeaderboardTeamBlock:
    """Teams in the ``leaderboard`` command."""

    def test_leaderboard_leads_with_teams(self, crew):
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        reply = _send(repo, settings, "leaderboard")
        teams_at, players_at = _block_order(
            reply, "Team standings (week):", "Week so far —"
        )
        assert teams_at < players_at

    def test_leaderboard_unchanged_without_teams(self, crew):
        repo, settings, _ = crew
        reply = _send(repo, settings, "leaderboard")
        assert "Team standings" not in reply
        assert reply.startswith("Week so far —")

    def test_per_game_leaderboard_has_no_team_block(self, crew):
        """``leaderboard queens`` is a single-game view; the team block
        is week-wide, so mixing them would misread as a per-game team
        standing."""
        repo, settings, _ = crew
        _send(repo, settings, "team Reds: Alice, Bob")
        assert "Team standings" not in _send(repo, settings, "leaderboard queens")


class TestCompactDailySections:
    """The daily per-game block folds onto one line per game."""

    def test_one_line_per_game(self, crew):
        repo, settings, _ = crew
        body, _targets = render_daily(
            repo, settings, LA_TODAY, group_id=repo.default_group.id, now=NOW
        )
        game_lines = [ln for ln in body.splitlines() if ln.startswith("Queens #")]
        assert len(game_lines) == 1
        # All four players on that single line.
        assert game_lines[0].count(" · ") == 3
        for name in ("Alice", "Bob", "Carol", "Dave"):
            assert name in game_lines[0]

    def test_scores_and_points_both_survive_the_fold(self, crew):
        repo, settings, players = crew
        body, _targets = render_daily(
            repo, settings, LA_TODAY, group_id=repo.default_group.id, now=NOW
        )
        line = next(ln for ln in body.splitlines() if ln.startswith("Queens #"))
        # "<name> <formatted score> (<points>)" — Alice is fastest.
        assert re.search(r"Alice \d+:\d\d \(\d", line)

    def test_recap_is_materially_shorter(self, crew):
        """The whole point of the fold. A 4-player, 2-game group used
        to spend 12 lines on the per-game block; now it spends 2."""
        repo, settings, _ = crew
        body, _targets = render_daily(
            repo, settings, LA_TODAY, group_id=repo.default_group.id, now=NOW
        )
        per_game = body.split("Game standings")[0]
        assert len(per_game.splitlines()) < 8
