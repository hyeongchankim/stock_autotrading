"""Telegram notifications for events worth knowing about away from the
screen - daily loss halt, order fills, failed protective exits, cycle
crashes. Never raises: a notification failure must not take down a
trading cycle. Silently no-ops if TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID
aren't configured - this is an optional feature, not required to run the
bot.

Setup: message @BotFather on Telegram to create a bot and get a token,
then message your new bot once and fetch
https://api.telegram.org/bot<TOKEN>/getUpdates to find your chat id. Set
both as environment variables (never hardcode them):
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger("utils.notify")

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT_SECONDS = 10


def send_notification(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    try:
        response = requests.post(
            _API_URL.format(token=token),
            json={"chat_id": chat_id, "text": message},
            timeout=_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            logger.warning("Telegram notification failed: %s %s", response.status_code, response.text)
    except requests.exceptions.RequestException as exc:
        logger.warning("Telegram notification failed: %s", exc)
