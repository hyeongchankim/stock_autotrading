"""Shared logging setup: console + logs/trading.log."""
from __future__ import annotations

import logging
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def setup_logging(level: int = logging.INFO) -> None:
    _LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(_LOG_DIR / "trading.log", encoding="utf-8"),
        ],
    )
