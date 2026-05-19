"""Badge / achievement system.

Badges are persistent milestones stored in the ``badges`` DB table.
Each check is idempotent — calling it multiple times never awards the
same badge twice (the repo layer enforces uniqueness).

Public entry points:
    :func:`check_badges_after_submission` — run after every score insert
    :func:`check_badges_after_weekly_wrap` — run after Sunday wrap
    :func:`notify_new_badges` — DM player for newly earned badges
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Dict, FrozenSet, List, Optional, Sequence

from .config import Settings
from .db import Badge, Player, Repository, ScoreRow
from .parsers import GAME_DISPLAY
from .scoring import PlayerWeeklyStats, weekly_leaderboard
from .sender import send_dm

logger = logging.getLogger(__name__)

# Per-game speedrunner threshold in seconds.
_SPEEDRUNNER_THRESHOLDS: Dict[str, int] = {
    "zip": 5,
    "tango": 30,
    "queens": 20,
    "crossclimb": 15,
    "mini_sudoku": 30,
    "patches": 30,
}

_BADGE_DISPLAY: Dict[str, str] = {
    "centurion": "🎖 Centurion",
    "speedrunner": "⚡ Speedrunner",
    "hot_streak": "🔥 Hot Streak",
    "perfect_week": "🌟 Perfect Week",
    "podium": "🏆 Podium",
}

_BADGE_DESCRIPTIONS: Dict[str, str] = {
    "centurion": "100 submissions for one game",
    "speedrunner": "sub-threshold time on a time-based game",
    "hot_streak": "submitted every day for 14 days straight",
    "perfect_week": "every game, every day for a full week",
    "podium": "finished top 3 in a weekly wrap",
}

_BADGE_MESSAGES: Dict[str, str] = {
    "centurion": "🎖 Centurion badge unlocked! You've played {game} 100 times. Committed.",
    "speedrunner": "⚡ Speedrunner badge unlocked! Sub-threshold time on {game}. Rapid.",
    "hot_streak": "🔥 Hot Streak badge! 14 days without missing a day. Unstoppable.",
    "perfect_week": "🌟 Perfect Week badge! Every game, every day. Flawless.",
    "podium": "🏆 Podium badge! You finished in the top 3 this week. Well earned.",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _check_centurion(
    repo: Repository,
    *,
    player_id: int,
    group_id: int,
    game: str,
) -> Optional[Badge]:
    """Award centurion badge if player has >= 100 submissions for game."""
    try:
        all_scores = repo.list_player_scores(player_id, group_id=group_id)
    except Exception:
        return None
    count = sum(1 for s in all_scores if s.game == game)
    if count < 100:
        return None
    awarded = repo.award_badge(
        player_id=player_id, group_id=group_id,
        badge_kind="centurion", game=game, earned_at=_now_utc(),
    )
    if not awarded:
        return None
    return next(
        (b for b in repo.list_unnotified_badges(player_id=player_id, group_id=group_id)
         if b.badge_kind == "centurion" and b.game == game),
        None,
    )


def _check_speedrunner(
    repo: Repository,
    *,
    player_id: int,
    group_id: int,
    game: str,
    new_raw: int,
) -> Optional[Badge]:
    """Award speedrunner badge if new_raw is below the threshold for game."""
    threshold = _SPEEDRUNNER_THRESHOLDS.get(game)
    if threshold is None or new_raw >= threshold:
        return None
    awarded = repo.award_badge(
        player_id=player_id, group_id=group_id,
        badge_kind="speedrunner", game=game, earned_at=_now_utc(),
    )
    if not awarded:
        return None
    return next(
        (b for b in repo.list_unnotified_badges(player_id=player_id, group_id=group_id)
         if b.badge_kind == "speedrunner" and b.game == game),
        None,
    )


def _check_hot_streak(
    repo: Repository,
    *,
    player_id: int,
    group_id: int,
    today: date,
) -> Optional[Badge]:
    """Award hot_streak badge if player submitted every day for 14 days."""
    from .puzzles import la_date
    try:
        all_scores = repo.list_player_scores(player_id, group_id=group_id)
    except Exception:
        return None
    days_played = {s.puzzle_date for s in all_scores}
    # Check that today and the 13 days before it are all in days_played.
    from datetime import timedelta
    required = {today - timedelta(days=i) for i in range(14)}
    if not required.issubset(days_played):
        return None
    awarded = repo.award_badge(
        player_id=player_id, group_id=group_id,
        badge_kind="hot_streak", game=None, earned_at=_now_utc(),
    )
    if not awarded:
        return None
    return next(
        (b for b in repo.list_unnotified_badges(player_id=player_id, group_id=group_id)
         if b.badge_kind == "hot_streak"),
        None,
    )


def _check_perfect_week(
    repo: Repository,
    *,
    player_id: int,
    group_id: int,
    week_start: date,
    week_end: date,
    enabled_games: FrozenSet[str],
) -> Optional[Badge]:
    """Award perfect_week if player submitted every enabled game every Mon–Sun."""
    from datetime import timedelta
    try:
        scores = repo.list_player_scores(player_id, group_id=group_id)
    except Exception:
        return None
    week_scores = {
        (s.game, s.puzzle_date)
        for s in scores
        if week_start <= s.puzzle_date <= week_end and s.game in enabled_games
    }
    required = {
        (g, week_start + timedelta(days=d))
        for g in enabled_games
        for d in range(7)
    }
    if not required.issubset(week_scores):
        return None
    key = f"perfect_week:{week_start.isoformat()}"
    # Use badge_kind with week encoded in game field to allow multiple perfect weeks.
    awarded = repo.award_badge(
        player_id=player_id, group_id=group_id,
        badge_kind="perfect_week", game=week_start.isoformat(), earned_at=_now_utc(),
    )
    if not awarded:
        return None
    return next(
        (b for b in repo.list_unnotified_badges(player_id=player_id, group_id=group_id)
         if b.badge_kind == "perfect_week" and b.game == week_start.isoformat()),
        None,
    )


def check_badges_after_submission(
    repo: Repository,
    *,
    player_id: int,
    group_id: int,
    game: str,
    new_raw: int,
    today: date,
    enabled_games: FrozenSet[str],
) -> List[Badge]:
    """Run badge checks relevant after a score submission.
    Returns list of newly awarded Badge objects."""
    new_badges: List[Badge] = []

    for check_fn, kwargs in [
        (_check_centurion, dict(player_id=player_id, group_id=group_id, game=game)),
        (_check_speedrunner, dict(player_id=player_id, group_id=group_id, game=game, new_raw=new_raw)),
        (_check_hot_streak, dict(player_id=player_id, group_id=group_id, today=today)),
    ]:
        try:
            badge = check_fn(repo, **kwargs)
            if badge is not None:
                new_badges.append(badge)
        except Exception:
            logger.exception("badge check %s failed", check_fn.__name__)

    return new_badges


def check_badges_after_weekly_wrap(
    repo: Repository,
    *,
    group_id: int,
    week_start: date,
    week_end: date,
    leaderboard: List[PlayerWeeklyStats],
    enabled_games: FrozenSet[str],
) -> Dict[int, List[Badge]]:
    """Run podium + perfect_week checks after weekly wrap.
    Returns {player_id: [newly awarded badges]}."""
    results: Dict[int, List[Badge]] = {}

    for i, entry in enumerate(leaderboard, start=1):
        player_badges: List[Badge] = []

        # Podium badge (top 3).
        if i <= 3:
            try:
                awarded = repo.award_badge(
                    player_id=entry.player_id, group_id=group_id,
                    badge_kind="podium",
                    game=week_start.isoformat(),
                    earned_at=_now_utc(),
                )
                if awarded:
                    badge = next(
                        (b for b in repo.list_unnotified_badges(
                            player_id=entry.player_id, group_id=group_id)
                         if b.badge_kind == "podium" and b.game == week_start.isoformat()),
                        None,
                    )
                    if badge:
                        player_badges.append(badge)
            except Exception:
                logger.exception("podium badge check failed for player %s", entry.player_id)

        # Perfect week badge.
        try:
            pw = _check_perfect_week(
                repo,
                player_id=entry.player_id,
                group_id=group_id,
                week_start=week_start,
                week_end=week_end,
                enabled_games=enabled_games,
            )
            if pw is not None:
                player_badges.append(pw)
        except Exception:
            logger.exception("perfect_week badge check failed for player %s", entry.player_id)

        if player_badges:
            results[entry.player_id] = player_badges

    return results


def notify_new_badges(
    repo: Repository,
    settings: Settings,
    *,
    player: Player,
    badges: List[Badge],
) -> None:
    """DM the player for each unnotified badge and mark them notified."""
    for badge in badges:
        try:
            game_label = GAME_DISPLAY.get(badge.game or "", badge.game or "")
            msg_template = _BADGE_MESSAGES.get(badge.badge_kind, "Badge unlocked: {kind}.")
            body = msg_template.format(
                game=game_label,
                kind=badge.badge_kind,
            )
            send_dm(settings, player.whatsapp_id, body)
        except Exception:
            logger.exception("Failed to send badge DM for badge %s", badge.id)

    badge_ids = [b.id for b in badges]
    try:
        repo.mark_badges_notified(badge_ids)
    except Exception:
        logger.exception("Failed to mark badges notified: %s", badge_ids)
