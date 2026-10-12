"""Universe-wide L1 scan for the forward test (screening/gap_tag.py explains L1).

The 15:20 candidate list (KIS ranking page 1 -> 1차 필터) is ~10 large caps that repeat every day, while L1
("volume surge + gap-up above the trend line") hits ~3-4 *different* names a day across the 347-name universe
(0 of the first 40 logged candidates were L1). To measure L1's overnight edge it must be evaluated on the whole
universe: `scan_l1_hits` checks every symbol's still-open daily bar and returns the hits, which
`forward_log.record_l1_hits` logs as extra rows (source = "l1_universe") so the usual fill/report path measures them.

Cost: one daily-bar request per symbol (~0.5 s each, a few in parallel) - run it AFTER the day's trading so it can
never delay an order. Observation only; it never raises (a failed symbol is skipped, a deadline stops the scan).
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from screening.gap_tag import l1_features

logger = logging.getLogger("screening.l1_universe")


def _evaluate(feed, symbol: str, as_of: date, lookback: int):
    """-> (state, row): state is "hit" / "miss" / "skip" (no bar for as_of: halted, holiday) / "error"."""
    try:
        bars = feed.get_ohlcv(symbol, "1d", lookback)
        if bars.empty or bars.index[-1].date() != as_of:
            return "skip", None
        features = l1_features(bars, bars.index[-1])
        if features is None:
            return "skip", None
        if not features["l1"]:
            return "miss", None
        last, prev = bars.iloc[-1], bars.iloc[-2]
        price, volume = float(last["close"]), int(last["volume"])
        return "hit", {
            "symbol": symbol, "price": price, "change_pct": round((price / float(prev["close"]) - 1) * 100, 2),
            "volume": volume, "trading_value": price * volume, "features": features,
        }
    except Exception as exc:  # noqa: BLE001 - observation must never break trading
        logger.debug("l1 universe: %s failed (%s)", symbol, exc)
        return "error", None


def scan_l1_hits(
    feed, symbols: list[str], as_of: date, lookback: int = 70, workers: int = 3, max_seconds: float = 420.0,
) -> tuple[list[dict], dict]:
    """Evaluates L1 for every symbol. Returns (hit rows, stats) where stats counts hit/miss/skip/error/unvisited
    (symbols not reached before `max_seconds`). Rows: symbol, price, change_pct, volume, trading_value, features."""
    start = time.monotonic()

    def run(symbol: str):
        if time.monotonic() - start > max_seconds:
            return "unvisited", None
        return _evaluate(feed, symbol, as_of, lookback)

    stats = {"hit": 0, "miss": 0, "skip": 0, "error": 0, "unvisited": 0}
    hits: list[dict] = []
    with ThreadPoolExecutor(workers) as pool:
        for state, row in pool.map(run, symbols):
            stats[state] += 1
            if row is not None:
                hits.append(row)
    if stats["error"] or stats["unvisited"]:
        logger.warning("l1 universe scan incomplete: %s", stats)
    return hits, stats
