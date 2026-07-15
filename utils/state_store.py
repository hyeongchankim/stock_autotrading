"""Tiny JSON-backed persistence for state that must survive across separate
`python main.py --mode paper` invocations - e.g. a scheduler running the
process repeatedly through a trading day. Used for RiskManager's daily loss
counters and the hybrid Buy & Hold sleeve's purchase record (see
risk/risk_manager.py and portfolio/buy_and_hold.py). Not used by backtest,
which is a single in-process run with no need to persist anything.
"""
from __future__ import annotations

import json
from pathlib import Path


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
