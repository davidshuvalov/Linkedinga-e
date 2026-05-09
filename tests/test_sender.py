"""Tests for ``app.sender`` — Twilio delivery with group → DM fallback.

All Twilio client calls are mocked; no real messages are sent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.sender import send_recap

_EG = frozenset({"queens", "tango", "zip", "patches", "mini_sudoku"})

BASE = Settings(
    twilio_account_sid="ACfake",
    twilio_auth_token="fake_token",
    twilio_whatsapp_from="whatsapp:+14155238886",
    twilio_recap_to="whatsapp:+61400000099",
    twilio_status_callback_url="",
    supabase_url="",
    supabase_key="",
    timezone_name="Australia/Sydney",
    enabled_games=_EG,
)

NO_TWILIO = Settings(
    twilio_account_sid="",
    twilio_auth_token="",
    twilio_whatsapp_from="",
    twilio_recap_to="",
    twilio_status_callback_url="",
    supabase_url="",
    supabase_key="",
    timezone_name="Australia/Sydney",
    enabled_games=_EG,
)


class TestSendRecap:
    def test_no_twilio_prints_to_stdout(self, capsys):
        send_recap(NO_TWILIO, "hello world")
        out = capsys.readouterr().out
        assert "hello world" in out

    @patch("app.sender._send_one", return_value=True)
    def test_group_post_succeeds_on_first_try(self, mock_send):
        send_recap(BASE, "recap text", dm_targets=["whatsapp:+1"])
        mock_send.assert_called_once_with(
            BASE, "whatsapp:+61400000099", "recap text"
        )

    @patch("app.sender._send_one")
    def test_falls_back_to_dms_when_group_fails(self, mock_send):
        # Group post fails, DM succeeds
        mock_send.side_effect = [False, True, True]
        targets = ["whatsapp:+1", "whatsapp:+2"]
        send_recap(BASE, "recap text", dm_targets=targets)

        assert mock_send.call_count == 3
        # First call: group
        assert mock_send.call_args_list[0][0][1] == "whatsapp:+61400000099"
        # Next two: individual DMs
        assert mock_send.call_args_list[1][0][1] == "whatsapp:+1"
        assert mock_send.call_args_list[2][0][1] == "whatsapp:+2"

    @patch("app.sender._send_one", return_value=True)
    def test_no_recap_to_goes_straight_to_dms(self, mock_send):
        no_group = Settings(
            twilio_account_sid="ACfake",
            twilio_auth_token="fake_token",
            twilio_whatsapp_from="whatsapp:+14155238886",
            twilio_recap_to="",
            twilio_status_callback_url="",
            supabase_url="",
            supabase_key="",
            timezone_name="Australia/Sydney",
            enabled_games=_EG,
        )
        send_recap(no_group, "recap", dm_targets=["whatsapp:+1"])
        mock_send.assert_called_once_with(no_group, "whatsapp:+1", "recap")

    @patch("app.sender._send_one", return_value=True)
    def test_per_group_recap_to_overrides_settings(self, mock_send):
        """When a group has its own ``recap_to``, it wins over the
        global ``settings.twilio_recap_to``. Lets new groups get DM
        fan-out (``group_recap_to=None``) while the default group
        keeps the existing global target."""
        send_recap(
            BASE, "recap",
            dm_targets=["whatsapp:+1"],
            group_recap_to="whatsapp:+groupB",
        )
        mock_send.assert_called_once_with(BASE, "whatsapp:+groupB", "recap")

    @patch("app.sender._send_one", return_value=True)
    def test_group_recap_to_none_skips_group_post(self, mock_send):
        """A new group that hasn't set its own ``recap_to`` passes
        ``None`` and gets DM fan-out — the default group's global
        ``twilio_recap_to`` is NOT used."""
        send_recap(
            BASE, "recap",
            dm_targets=["whatsapp:+1"],
            group_recap_to=None,
        )
        # Skipped the group post and went straight to DMs.
        mock_send.assert_called_once_with(BASE, "whatsapp:+1", "recap")
