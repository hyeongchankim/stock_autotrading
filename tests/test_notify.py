"""Sanity checks for Telegram notifications - must never raise, must no-op
without credentials.

Run with: pytest
(or: python -m unittest tests.test_notify)
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import requests

from utils.notify import send_notification


class TestSendNotification(unittest.TestCase):
    @patch("utils.notify.requests.post")
    def test_noop_without_credentials(self, mock_post):
        with patch.dict("os.environ", {}, clear=True):
            send_notification("hello")
        mock_post.assert_not_called()

    @patch("utils.notify.requests.post")
    def test_sends_when_configured(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}, clear=True):
            send_notification("hello world")

        mock_post.assert_called_once()
        sent_url = mock_post.call_args.args[0]
        sent_json = mock_post.call_args.kwargs["json"]
        self.assertIn("tok", sent_url)
        self.assertEqual(sent_json["chat_id"], "123")
        self.assertEqual(sent_json["text"], "hello world")

    @patch("utils.notify.requests.post")
    def test_never_raises_on_http_error_status(self, mock_post):
        mock_post.return_value = MagicMock(status_code=401, text="unauthorized")
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}, clear=True):
            send_notification("hello")  # must not raise

    @patch("utils.notify.requests.post", side_effect=requests.exceptions.ConnectionError("network down"))
    def test_never_raises_on_network_error(self, mock_post):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}, clear=True):
            send_notification("hello")  # must not raise


if __name__ == "__main__":
    unittest.main()
