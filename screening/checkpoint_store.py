"""Tracks which symbols showed up as screening candidates across the day's
checkpoints (11:00/13:30/15:20 by default - config.yaml's discovery.
checkpoints), so a stock that only spiked in the last few minutes before
close doesn't get the same weight as one that's been strong all day.
Backed by the same StateStore JSON pattern as risk_manager/buy_and_hold
state, just a separate file so a corrupt/reset discovery state can never
touch the paper-trading position state.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

from utils.state_store import StateStore


def record_checkpoint(store: StateStore, as_of: date, candidates: list[str]) -> None:
    """Appends this checkpoint's candidate list. A new day starts a fresh
    record - yesterday's checkpoints must never carry into today's count.
    """
    state = store.load()
    date_str = as_of.isoformat()
    if state.get("date") != date_str:
        state = {"date": date_str, "checkpoints": []}
    state["checkpoints"].append(candidates)
    store.save(state)


def persistent_candidates(store: StateStore, as_of: date, min_checkpoints: int = 2) -> set[str]:
    """Symbols that appeared in at least min_checkpoints of today's
    recorded checkpoints. Returns an empty set if today has no recorded
    checkpoints yet (e.g. state is stale from a previous day).
    """
    state = store.load()
    if state.get("date") != as_of.isoformat():
        return set()
    counts = Counter(symbol for checkpoint in state.get("checkpoints", []) for symbol in checkpoint)
    return {symbol for symbol, n in counts.items() if n >= min_checkpoints}
