"""Shared logging setup: console + logs/trading.log.

trading.log rotates at midnight and keeps 30 days of backups
(trading.log.YYYY-MM-DD) - without this it grows forever under the
15-minute scheduled task (see run_paper_cycle.bat), which runs
unattended for weeks/months at a time.
"""
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def setup_logging(level: int = logging.INFO) -> None:
    _LOG_DIR.mkdir(exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        _LOG_DIR / "trading.log", when="midnight", backupCount=30, encoding="utf-8"
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            file_handler,
        ],
    )
