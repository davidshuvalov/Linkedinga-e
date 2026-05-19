"""Narrative weekly wrap generator.

:func:`build_narrative_context` extracts story-worthy moments from a week's
scores. :func:`render_narrative` picks a template and fills it in.

Kept entirely pure (no I/O) so it can be tested without a repo.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import FrozenSet, List, Optional, Sequence, Tuple

from .db import ScoreRow
from .parsers import GAME_DISPLAY
from .scoring import weekly_leaderboard


@dataclass(frozen=True)
class NarrativeContext:
    winner_name: str
    winner_points: float
    runner_up_name: Optional[str]
    runner_up_points: float
    gap: float
    biggest_mover_name: Optional[str]       # most positions gained vs prior week
    biggest_mover_positions: int
    closest_pair: Tuple[str, str]           # two consecutive players with smallest gap
    closest_pair_gap: float
    game_mvp_name: str                      # most single-game points in a round
    game_mvp_game: str
    game_mvp_score: float
    total_players: int


_NARRATIVE_TEMPLATES: Tuple[str, ...] = (
    "{winner} took this week with {points} pts. {runner_up_blurb}"
    "{mover_blurb}"
    "Closest battle: {close1} vs {close2} ({close_gap} pts apart).",

    "Week {points} pts — that's {winner}'s tally and it was enough. "
    "{runner_up_blurb}"
    "Biggest move: {mover_blurb2}"
    "{mvp_name} was game MVP on {mvp_game} this week.",

    "{winner} dominated: {points} pts at the top. "
    "{runner_up_blurb}"
    "{close1} and {close2} were inseparable all week ({close_gap} pts). "
    "{mover_blurb}",

    "This week belonged to {winner} ({points} pts). "
    "{mover_blurb}"
    "The tightest battle was {close1} vs {close2} — just {close_gap} pts in it. "
    "{mvp_name} shone on {mvp_game}.",

    "{winner} came, saw, and conquered: {points} pts. "
    "{runner_up_blurb}"
    "{mvp_name} put in a standout {mvp_game} showing. "
    "{close1} vs {close2} went right to the wire ({close_gap} pts).",

    "Week summary: {winner} leads all comers on {points} pts. "
    "{mover_blurb}"
    "Hottest rivalry this week: {close1} and {close2} separated by just {close_gap} pts.",

    "{winner} finishes on top ({points} pts). "
    "{runner_up_blurb}"
    "{mvp_name} was the standout on {mvp_game}. "
    "{mover_blurb}",

    "It's {winner}'s week — {points} pts and no one came close. "
    "Well, {close1} and {close2} came close to each other ({close_gap} pts). "
    "{mover_blurb}",

    "{winner} takes the week ({points} pts). "
    "{mover_blurb}"
    "{close1} and {close2} fought hard — only {close_gap} pts between them. "
    "MVP nod to {mvp_name} on {mvp_game}.",

    "Final standings: {winner} on top with {points} pts. "
    "{runner_up_blurb}"
    "Biggest climber: {mover_blurb2}"
    "Narrowest gap: {close1} vs {close2} ({close_gap} pts).",

    "{winner} wins Week totals — {points} pts. "
    "{close1} and {close2} made it interesting ({close_gap} pts apart). "
    "{mvp_name} was the one to watch on {mvp_game}. "
    "{mover_blurb}",

    "Wrap: {winner} ({points} pts) finishes first. "
    "{mover_blurb}"
    "{mvp_name} had the best single-game display ({mvp_game}). "
    "Closest finish: {close1} and {close2}, {close_gap} pts.",
)


def build_narrative_context(
    week_scores: Sequence[ScoreRow],
    prior_week_scores: Sequence[ScoreRow],
    enabled_games: FrozenSet[str],
) -> Optional[NarrativeContext]:
    """Derive story elements from this week's scores.

    Returns ``None`` when fewer than 2 players submitted this week.
    """
    filtered = [s for s in week_scores if s.game in enabled_games]
    if not filtered:
        return None

    lb = weekly_leaderboard(filtered)
    if len(lb) < 2:
        return None

    winner = lb[0]
    runner_up = lb[1]
    gap = round(winner.total_points - runner_up.total_points, 1)

    # Biggest mover vs prior week.
    biggest_mover_name: Optional[str] = None
    biggest_mover_positions = 0
    if prior_week_scores:
        prior_filtered = [s for s in prior_week_scores if s.game in enabled_games]
        prior_lb = weekly_leaderboard(prior_filtered)
        prior_ranks = {e.player_id: i for i, e in enumerate(prior_lb, 1)}
        cur_ranks = {e.player_id: i for i, e in enumerate(lb, 1)}
        best_gain = 0
        for pid, cur_rank in cur_ranks.items():
            prior_rank = prior_ranks.get(pid)
            if prior_rank is not None:
                gain = prior_rank - cur_rank
                if gain > best_gain:
                    best_gain = gain
                    biggest_mover_name = next(
                        e.player_name for e in lb if e.player_id == pid
                    )
                    biggest_mover_positions = gain

    # Closest consecutive pair.
    closest_pair: Tuple[str, str] = (lb[-2].player_name, lb[-1].player_name)
    closest_gap = round(lb[-2].total_points - lb[-1].total_points, 1)
    for i in range(len(lb) - 1):
        pair_gap = round(lb[i].total_points - lb[i + 1].total_points, 1)
        if pair_gap <= closest_gap:
            closest_gap = pair_gap
            closest_pair = (lb[i].player_name, lb[i + 1].player_name)

    # Game MVP: player with highest single-round points.
    from .scoring import assign_daily_points
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for s in filtered:
        groups[(s.game, s.puzzle_no)].append(s)

    mvp_pid: Optional[int] = None
    mvp_pts = 0.0
    mvp_game_key = ""
    for (game, _), group in groups.items():
        pts_map = assign_daily_points(group)
        for pid, pts in pts_map.items():
            if pts > mvp_pts:
                mvp_pts = pts
                mvp_pid = pid
                mvp_game_key = game

    mvp_name = winner.player_name
    if mvp_pid is not None:
        match = next((e for e in lb if e.player_id == mvp_pid), None)
        if match:
            mvp_name = match.player_name

    return NarrativeContext(
        winner_name=winner.player_name,
        winner_points=winner.total_points,
        runner_up_name=runner_up.player_name,
        runner_up_points=runner_up.total_points,
        gap=gap,
        biggest_mover_name=biggest_mover_name,
        biggest_mover_positions=biggest_mover_positions,
        closest_pair=closest_pair,
        closest_pair_gap=closest_gap,
        game_mvp_name=mvp_name,
        game_mvp_game=GAME_DISPLAY.get(mvp_game_key, mvp_game_key),
        game_mvp_score=mvp_pts,
        total_players=len(lb),
    )


def render_narrative(ctx: NarrativeContext, seed: Optional[int] = None) -> str:
    """Pick a template and fill it from ``ctx``."""
    rng = random.Random(seed)
    tmpl = rng.choice(_NARRATIVE_TEMPLATES)

    runner_up_blurb = (
        f"{ctx.runner_up_name} pushed hard — {ctx.runner_up_points} pts, "
        f"{ctx.gap} behind. "
        if ctx.runner_up_name else ""
    )

    if ctx.biggest_mover_name and ctx.biggest_mover_positions > 0:
        pos_word = "spot" if ctx.biggest_mover_positions == 1 else "spots"
        mover_blurb = (
            f"{ctx.biggest_mover_name} climbed {ctx.biggest_mover_positions} {pos_word}. "
        )
        mover_blurb2 = (
            f"{ctx.biggest_mover_name} (+{ctx.biggest_mover_positions} {pos_word}). "
        )
    else:
        mover_blurb = ""
        mover_blurb2 = "no one moved much from last week. "

    return tmpl.format(
        winner=ctx.winner_name,
        points=ctx.winner_points,
        runner_up_blurb=runner_up_blurb,
        mover_blurb=mover_blurb,
        mover_blurb2=mover_blurb2,
        close1=ctx.closest_pair[0],
        close2=ctx.closest_pair[1],
        close_gap=ctx.closest_pair_gap,
        mvp_name=ctx.game_mvp_name,
        mvp_game=ctx.game_mvp_game,
    ).strip()
