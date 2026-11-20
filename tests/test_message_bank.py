"""Guards for the extra copy in :mod:`app.message_bank`.

Every line must render with exactly the placeholders its pool's
renderer supplies, must actually be wired into that pool, and must not
duplicate an existing line.
"""

from __future__ import annotations

import string

import pytest

from app import jobs, message_bank, notifications, scheduler

_TRIG = {"name", "game", "new"}
ALLOWED = {
    "NEW_PB": _TRIG | {"prior"},
    "TIED_PB": _TRIG,
    "NEW_WORST": _TRIG | {"prior"},
    "TIED_WORST": _TRIG,
    "BEST_OF_DAY": _TRIG,
    "WORST_OF_DAY": _TRIG,
    "ALL_TIME_RECORD": _TRIG | {"prior", "prior_holder"},
    "ALL_TIME_ANTI_RECORD": _TRIG | {"prior", "prior_holder"},
    "GROUP_DOW_RECORD": _TRIG | {"prior", "prior_holder", "weekday"},
    "DOW_PB": _TRIG | {"prior", "weekday"},
    "DOW_WORST": _TRIG | {"prior", "weekday"},
    "YEAR_PB": _TRIG | {"prior", "year"},
    "FIRST_TODAY": _TRIG,
    "NTH_BEST": _TRIG | {"rank"},
    "TOP_QUARTILE": _TRIG,
    "BOTTOM_QUARTILE": _TRIG,
    "DOW_TOP_QUARTILE": _TRIG | {"weekday"},
    "DOW_BOTTOM_QUARTILE": _TRIG | {"weekday"},
    "ABOVE_FLOOR_TODAY": _TRIG | {"prior", "prior_holder"},
    "RIVALRY": {"name", "rival", "weeks", "gap"},
    "PODIUM_STREAK": {"name", "weeks"},
    "VS_USUAL_BETTER": _TRIG | {"usual"},
    "VS_USUAL_WORSE": _TRIG | {"usual"},
    "VS_USUAL_PAR": _TRIG | {"usual"},
    "PHOTO_FINISH": {"p1", "p2", "gap", "game"},
    "COMEBACK": {"name", "prior_leader", "days_held", "day_word"},
    "MORNING_OPENERS_NO_PROGRESS": {"name"},
    "MORNING_OPENERS_PARTIAL": {"name", "played"},
    "MORNING_SIGN_OFFS": set(),
    "NUDGE_CTX_LEADER": {"name", "points"},
    "NUDGE_CTX_LAST_DAY": {"name", "period"},
    "NUDGE_CTX_STREAK": {"name", "game", "streak", "streak_plus_one"},
    "PRE_RESET_2H": {"name", "missing"},
    "PRE_RESET_1H": {"name", "missing"},
    "PRE_RESET_30MIN": {"name", "missing"},
    "PRE_RESET_5MIN": {"name", "missing"},
    "NEW_GAMES": set(),
    "BRAG": {"sender"},
    "GRIPE": {"sender"},
    "MISSING_TODAY_SINGULAR": {"names"},
    "MISSING_TODAY_PLURAL": {"names"},
}

TARGET = {
    "NEW_PB": notifications._NEW_PB_TEMPLATES,
    "TIED_PB": notifications._TIED_PB_TEMPLATES,
    "NEW_WORST": notifications._NEW_WORST_TEMPLATES,
    "TIED_WORST": notifications._TIED_WORST_TEMPLATES,
    "BEST_OF_DAY": notifications._BEST_OF_DAY_TEMPLATES,
    "WORST_OF_DAY": notifications._WORST_OF_DAY_TEMPLATES,
    "ALL_TIME_RECORD": notifications._ALL_TIME_RECORD_TEMPLATES,
    "ALL_TIME_ANTI_RECORD": notifications._ALL_TIME_ANTI_RECORD_TEMPLATES,
    "GROUP_DOW_RECORD": notifications._GROUP_DOW_RECORD_TEMPLATES,
    "DOW_PB": notifications._DOW_PB_TEMPLATES,
    "DOW_WORST": notifications._DOW_WORST_TEMPLATES,
    "YEAR_PB": notifications._YEAR_PB_TEMPLATES,
    "FIRST_TODAY": notifications._FIRST_TODAY_TEMPLATES,
    "NTH_BEST": notifications._NTH_BEST_TEMPLATES,
    "TOP_QUARTILE": notifications._TOP_QUARTILE_TEMPLATES,
    "BOTTOM_QUARTILE": notifications._BOTTOM_QUARTILE_TEMPLATES,
    "DOW_TOP_QUARTILE": notifications._DOW_TOP_QUARTILE_TEMPLATES,
    "DOW_BOTTOM_QUARTILE": notifications._DOW_BOTTOM_QUARTILE_TEMPLATES,
    "ABOVE_FLOOR_TODAY": notifications._ABOVE_FLOOR_TODAY_TEMPLATES,
    "RIVALRY": notifications._RIVALRY_TEMPLATES,
    "PODIUM_STREAK": notifications._PODIUM_STREAK_TEMPLATES,
    "VS_USUAL_BETTER": notifications._VS_USUAL_BETTER_TEMPLATES,
    "VS_USUAL_WORSE": notifications._VS_USUAL_WORSE_TEMPLATES,
    "VS_USUAL_PAR": notifications._VS_USUAL_PAR_TEMPLATES,
    "PHOTO_FINISH": notifications._PHOTO_FINISH_TEMPLATES,
    "COMEBACK": notifications._COMEBACK_TEMPLATES,
    "MORNING_OPENERS_NO_PROGRESS": jobs._MORNING_OPENERS_NO_PROGRESS,
    "MORNING_OPENERS_PARTIAL": jobs._MORNING_OPENERS_PARTIAL,
    "MORNING_SIGN_OFFS": jobs._MORNING_SIGN_OFFS,
    "NUDGE_CTX_LEADER": jobs._NUDGE_CTX_LEADER_TEMPLATES,
    "NUDGE_CTX_LAST_DAY": jobs._NUDGE_CTX_LAST_DAY_TEMPLATES,
    "NUDGE_CTX_STREAK": jobs._NUDGE_CTX_STREAK_TEMPLATES,
    "PRE_RESET_2H": jobs._PRE_RESET_TEMPLATES["2h"],
    "PRE_RESET_1H": jobs._PRE_RESET_TEMPLATES["1h"],
    "PRE_RESET_30MIN": jobs._PRE_RESET_TEMPLATES["30min"],
    "PRE_RESET_5MIN": jobs._PRE_RESET_TEMPLATES["5min"],
    "NEW_GAMES": jobs._NEW_GAMES_TEMPLATES,
    "BRAG": jobs._BRAG_TEMPLATES,
    "GRIPE": jobs._GRIPE_TEMPLATES,
    "MISSING_TODAY_SINGULAR": scheduler._MISSING_TODAY_SINGULAR_TEMPLATES,
    "MISSING_TODAY_PLURAL": scheduler._MISSING_TODAY_PLURAL_TEMPLATES,
}


def _bank_pools():
    return {
        k: v for k, v in vars(message_bank).items()
        if k.isupper() and isinstance(v, tuple)
    }


def test_every_pool_is_mapped():
    assert set(_bank_pools()) == set(ALLOWED) == set(TARGET)


def test_thousand_new_lines():
    assert sum(len(v) for v in _bank_pools().values()) == 1000


@pytest.mark.parametrize("key", sorted(ALLOWED))
def test_placeholders_render(key):
    allowed = ALLOWED[key]
    subs = {k: "X" for k in allowed}
    for line in getattr(message_bank, key):
        used = {f for _, f, _, _ in string.Formatter().parse(line) if f}
        assert used <= allowed, (key, line, used - allowed)
        line.format(**subs)


@pytest.mark.parametrize("key", sorted(TARGET))
def test_wired_in_without_duplicates(key):
    pool = TARGET[key]
    extra = getattr(message_bank, key)
    assert pool[-len(extra):] == extra
    assert len(set(pool)) == len(pool), key
