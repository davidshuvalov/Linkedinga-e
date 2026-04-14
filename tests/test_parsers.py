"""Unit tests for ``app.parsers``.

The share-text fixtures below are **placeholder first drafts** — they match
the TODO-marked shapes in ``app/parsers.py``. When you have real LinkedIn
share samples, paste them in here as additional parametrized cases and tune
the regexes to match; this file is the regression net.
"""

from __future__ import annotations

import pytest

from app.parsers import (
    ParsedScore,
    _time_to_seconds,
    looks_like_score,
    parse_any,
    parse_crossclimb,
    parse_pinpoint,
    parse_queens,
    parse_tango,
    parse_zip,
)

# ---------------------------------------------------------------------------
# _time_to_seconds
# ---------------------------------------------------------------------------


class TestTimeToSeconds:
    def test_plain_seconds(self):
        assert _time_to_seconds("45") == 45

    def test_mmss(self):
        assert _time_to_seconds("1:23") == 83

    def test_mmss_zero_minute(self):
        assert _time_to_seconds("0:45") == 45

    def test_mmss_double_digit_minute(self):
        assert _time_to_seconds("12:34") == 754

    def test_hhmmss(self):
        assert _time_to_seconds("1:00:00") == 3600

    def test_surrounding_whitespace(self):
        assert _time_to_seconds("  1:23  ") == 83

    def test_invalid_returns_none(self):
        assert _time_to_seconds("abc") is None

    def test_empty_returns_none(self):
        assert _time_to_seconds("") is None


# ---------------------------------------------------------------------------
# Queens
# ---------------------------------------------------------------------------


class TestParseQueens:
    def test_basic(self):
        text = "Queens #365 | 1:23\n♛ ♛ ♛\nlnkd.in/queens"
        assert parse_queens(text) == ParsedScore("queens", 365, 83, text)

    def test_case_insensitive(self):
        assert parse_queens("queens #1 | 0:30").raw_score == 30
        assert parse_queens("QUEENS #1 | 0:30").raw_score == 30

    def test_extra_emoji_and_whitespace(self):
        text = "  🎉  Queens   #42   |   2:05  🎉"
        r = parse_queens(text)
        assert r is not None
        assert r.puzzle_no == 42
        assert r.raw_score == 125

    def test_newline_between_name_and_number(self):
        r = parse_queens("Queens\n#7 | 0:55")
        assert r is not None
        assert r.puzzle_no == 7
        assert r.raw_score == 55

    def test_wrong_game_returns_none(self):
        assert parse_queens("Zip #1 | 0:30") is None

    def test_no_score_returns_none(self):
        assert parse_queens("Queens #1 was tough today") is None


# ---------------------------------------------------------------------------
# Tango
# ---------------------------------------------------------------------------


class TestParseTango:
    def test_basic(self):
        text = "Tango #123 | 0:45 and flawless\n☀️🌑🌑☀️\nlnkd.in/tango"
        r = parse_tango(text)
        assert r is not None
        assert r.game == "tango"
        assert r.puzzle_no == 123
        assert r.raw_score == 45

    def test_over_a_minute(self):
        r = parse_tango("Tango #2 | 2:30")
        assert r is not None
        assert r.raw_score == 150

    def test_wrong_game_returns_none(self):
        assert parse_tango("Queens #1 | 0:30") is None


# ---------------------------------------------------------------------------
# Pinpoint
# ---------------------------------------------------------------------------


class TestParsePinpoint:
    def test_basic(self):
        text = "Pinpoint #200 | 3 guesses\n📌 📌 ✅\nlnkd.in/pinpoint"
        assert parse_pinpoint(text) == ParsedScore("pinpoint", 200, 3, text)

    def test_one_guess_singular(self):
        r = parse_pinpoint("Pinpoint #1 | 1 guess")
        assert r is not None
        assert r.raw_score == 1

    def test_five_guesses_boundary(self):
        r = parse_pinpoint("Pinpoint #9 | 5 guesses")
        assert r is not None
        assert r.raw_score == 5

    def test_rejects_out_of_range_high(self):
        assert parse_pinpoint("Pinpoint #1 | 10 guesses") is None

    def test_rejects_zero(self):
        assert parse_pinpoint("Pinpoint #1 | 0 guesses") is None

    def test_wrong_game_returns_none(self):
        assert parse_pinpoint("Queens #1 | 0:30") is None


# ---------------------------------------------------------------------------
# Crossclimb
# ---------------------------------------------------------------------------


class TestParseCrossclimb:
    def test_basic(self):
        text = "Crossclimb #77 | 1:45\n🪜\nlnkd.in/crossclimb"
        r = parse_crossclimb(text)
        assert r is not None
        assert r.game == "crossclimb"
        assert r.puzzle_no == 77
        assert r.raw_score == 105

    def test_wrong_game_returns_none(self):
        assert parse_crossclimb("Zip #1 | 0:30") is None


# ---------------------------------------------------------------------------
# Zip
# ---------------------------------------------------------------------------


class TestParseZip:
    def test_basic(self):
        text = "Zip #88 | 0:42 🏁\nWith 0 backtracks\nlnkd.in/zip"
        r = parse_zip(text)
        assert r is not None
        assert r.game == "zip"
        assert r.puzzle_no == 88
        assert r.raw_score == 42

    def test_wrong_game_returns_none(self):
        assert parse_zip("Queens #1 | 0:30") is None


# ---------------------------------------------------------------------------
# parse_any dispatcher
# ---------------------------------------------------------------------------


class TestParseAny:
    @pytest.mark.parametrize(
        "text,expected_game,expected_puzzle,expected_raw",
        [
            ("Queens #1 | 0:30", "queens", 1, 30),
            ("Tango #2 | 0:45", "tango", 2, 45),
            ("Pinpoint #3 | 2 guesses", "pinpoint", 3, 2),
            ("Crossclimb #4 | 1:00", "crossclimb", 4, 60),
            ("Zip #5 | 0:42 🏁", "zip", 5, 42),
        ],
    )
    def test_dispatches_to_correct_game(
        self, text, expected_game, expected_puzzle, expected_raw
    ):
        r = parse_any(text)
        assert r is not None
        assert r.game == expected_game
        assert r.puzzle_no == expected_puzzle
        assert r.raw_score == expected_raw

    def test_unknown_message_returns_none(self):
        assert parse_any("hey what's up") is None

    def test_empty_returns_none(self):
        assert parse_any("") is None

    def test_chatty_message_with_distant_number_not_misparsed(self):
        # "Queens failed, tried Zip #1 | 0:30" — shouldn't be counted as a
        # Queens score. parse_any should return the Zip result.
        r = parse_any("Queens failed, tried Zip #1 | 0:30")
        assert r is not None
        assert r.game == "zip"
        assert r.puzzle_no == 1
        assert r.raw_score == 30


# ---------------------------------------------------------------------------
# looks_like_score
# ---------------------------------------------------------------------------


class TestLooksLikeScore:
    def test_matches_game_name(self):
        assert looks_like_score("Queens #1 | 0:30")

    def test_matches_casual_mention(self):
        assert looks_like_score("yo anyone doing tango today?")

    def test_matches_lnkd_in_link(self):
        assert looks_like_score("check this out lnkd.in/queens")

    def test_rejects_unrelated(self):
        assert not looks_like_score("dinner at 7?")

    def test_rejects_empty(self):
        assert not looks_like_score("")


# ---------------------------------------------------------------------------
# Regression fixtures: verbatim real share-text samples
# ---------------------------------------------------------------------------


class TestRealSamples:
    """Verbatim share-text pasted by the user, captured 2026-04.

    If any of these break, LinkedIn changed the format — update the regex
    in ``app/parsers.py`` and keep this class faithful to the new reality.
    """

    REAL_QUEENS = "Queens #714\n0:10 👑\nlnkd.in/queens."
    REAL_TANGO = "Tango #554\n0:35 🌗\nlnkd.in/tango."
    REAL_ZIP = "Zip #393\n0:09 🏁\nlnkd.in/zip."
    REAL_CROSSCLIMB = "Crossclimb #714\n1:44 🪜\nlnkd.in/crossclimb."
    REAL_PINPOINT = (
        "Pinpoint #714 | 4 guesses\n"
        "1\ufe0f\u20e3  | 2% match\n"
        "2\ufe0f\u20e3  | 9% match\n"
        "3\ufe0f\u20e3  | 3% match\n"
        "4\ufe0f\u20e3  | 100% match 📌\n"
        "lnkd.in/pinpoint."
    )

    def test_real_queens(self):
        assert parse_queens(self.REAL_QUEENS) == ParsedScore(
            "queens", 714, 10, self.REAL_QUEENS
        )

    def test_real_tango(self):
        assert parse_tango(self.REAL_TANGO) == ParsedScore(
            "tango", 554, 35, self.REAL_TANGO
        )

    def test_real_zip(self):
        assert parse_zip(self.REAL_ZIP) == ParsedScore(
            "zip", 393, 9, self.REAL_ZIP
        )

    def test_real_crossclimb(self):
        assert parse_crossclimb(self.REAL_CROSSCLIMB) == ParsedScore(
            "crossclimb", 714, 104, self.REAL_CROSSCLIMB
        )

    def test_real_pinpoint(self):
        assert parse_pinpoint(self.REAL_PINPOINT) == ParsedScore(
            "pinpoint", 714, 4, self.REAL_PINPOINT
        )

    @pytest.mark.parametrize(
        "attr,expected_game,expected_no,expected_raw",
        [
            ("REAL_QUEENS", "queens", 714, 10),
            ("REAL_TANGO", "tango", 554, 35),
            ("REAL_ZIP", "zip", 393, 9),
            ("REAL_CROSSCLIMB", "crossclimb", 714, 104),
            ("REAL_PINPOINT", "pinpoint", 714, 4),
        ],
    )
    def test_parse_any_dispatches_real_samples(
        self, attr, expected_game, expected_no, expected_raw
    ):
        text = getattr(self, attr)
        result = parse_any(text)
        assert result is not None, f"parse_any returned None for {expected_game}"
        assert result.game == expected_game
        assert result.puzzle_no == expected_no
        assert result.raw_score == expected_raw

    def test_pinpoint_keycap_digits_not_mismatched(self):
        """Make sure ``1️⃣``/``2️⃣``/``3️⃣``/``4️⃣`` rows aren't misread as a
        guess count. The ``4 guesses`` header is what should win."""
        r = parse_pinpoint(self.REAL_PINPOINT)
        assert r is not None
        assert r.raw_score == 4  # from "4 guesses", not the keycaps
