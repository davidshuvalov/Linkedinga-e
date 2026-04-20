"""Tests for ``app.sender`` — Twilio delivery with group → DM fallback.

All Twilio client calls are mocked; no real messages are sent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.sender import send_recap

BASE = Settings(
    twilio_account_sid="ACfake",
    twilio_auth_token="fake_token",
    twilio_whatsapp_from="whatsapp:+14155238886",
    twilio_recap_to="whatsapp:+61400000099",
    supabase_url="",
    supabase_key="",
    timezone_name="Australia/Sydney",
)

NO_TWILIO = Settings(
    twilio_account_sid="",
    twilio_auth_token="",
    twilio_whatsapp_from="",
    twilio_recap_to="",
    supabase_url="",
    supabase_key="",
    timezone_name="Australia/Sydney",
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
            supabase_url="",
            supabase_key="",
            timezone_name="Australia/Sydney",
        )
        send_recap(no_group, "recap", dm_targets=["whatsapp:+1"])
        mock_send.assert_called_once_with(no_group, "whatsapp:+1", "recap")
