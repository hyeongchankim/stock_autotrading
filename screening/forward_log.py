"""Forward test log for the 발굴형 종가매매 candidates.

The daily-bar backtests (backtest/overnight.py) rank movers by the FINAL close
and buy at the exact close - information the live 15:20 scan doesn't have. This
log records what the scan actually saw at ~15:20 (price, change%, rank) and later
fills in what happened: that day's final close, the next open and the next close.
Over weeks that measures the real 15:20 -> next-open edge, look-ahead removed.

Storage: one JSON file (utils.state_store.StateStore), gitignored like the other
local state. Every candidate that passed the 1차 필터 is logged, not just the
top few, so the analysis can pick any cut-off afterwards. Observation only - this
module never places orders.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time

import pandas as pd

from utils.state_store import StateStore

logger = logging.getLogger("screening.forward_log")

# a daily bar is only trusted once its value is final
_OPEN_FINAL = time(9, 5)    # opening auction done
_CLOSE_FINAL = time(15, 40)  # closing auction done and published
_OUTCOME_FIELDS = ("close", "open_next", "close_next")


def record_candidates(
    store: StateStore, as_of: date, candidates: pd.DataFrame, qualified: set[str], now: datetime | None = None
) -> int:
    """Logs today's scan (columns: symbol, name, price, change_pct, volume,
    trading_value), ranked by change% desc. Re-running the same day replaces that
    day's rows instead of duplicating them. Returns the number of rows logged.
    """
    now = now or datetime.now()
    ranked = candidates.sort_values("change_pct", ascending=False).to_dict("records")
    new = [
        {
            "date": as_of.isoformat(), "symbol": r["symbol"], "name": r["name"], "rank": i + 1,
            "price_scan": float(r["price"]), "change_pct": float(r["change_pct"]), "volume": int(r["volume"]),
            "trading_value": float(r["trading_value"]), "qualified": r["symbol"] in qualified,
            "scanned_at": now.isoformat(timespec="seconds"),
        }
        for i, r in enumerate(ranked)
    ]
    state = store.load()
    kept = [r for r in state.get("records", []) if r["date"] != as_of.isoformat()]
    state["records"] = kept + new
    store.save(state)
    return len(new)


def _pending(records: list[dict]) -> list[dict]:
    return [r for r in records if any(f not in r for f in _OUTCOME_FIELDS)]


def fill_outcomes(store: StateStore, feed, now: datetime | None = None, lookback: int = 30) -> int:
    """Fills close / open_next / close_next for logged rows from daily bars
    (`feed.get_ohlcv(symbol, "1d", lookback)`), skipping bars whose value isn't
    final yet (today's close before 15:40, today's open before 09:05). Runs at
    most once per calendar day; the next trading day is the first bar after the
    logged date, so holidays and weekends need no calendar. Never raises: a data
    problem must not take the trading run down. Returns the number of fields filled.
    """
    now = now or datetime.now()
    today = now.date()
    state = store.load()
    if state.get("last_fill") == today.isoformat():
        return 0
    records = state.get("records", [])

    def final(bar_date: date, cutoff: time) -> bool:
        return bar_date < today or (bar_date == today and now.time() >= cutoff)

    bars: dict[str, pd.DataFrame | None] = {}
    filled = 0
    failed = False
    for rec in _pending(records):
        symbol = rec["symbol"]
        if symbol not in bars:
            try:
                bars[symbol] = feed.get_ohlcv(symbol, "1d", lookback)
            except Exception as exc:  # noqa: BLE001 - observation must never break trading
                logger.warning("forward log: no bars for %s (%s)", symbol, exc)
                bars[symbol] = None
                failed = True
        df = bars[symbol]
        if df is None or df.empty:
            continue

        day = pd.Timestamp(rec["date"])
        if "close" not in rec and day in df.index and final(day.date(), _CLOSE_FINAL):
            rec["close"] = float(df.loc[day, "close"])
            filled += 1
        later = df.index[df.index > day]
        if len(later):
            nxt = later[0]
            if "open_next" not in rec and final(nxt.date(), _OPEN_FINAL):
                rec["open_next"], rec["next_date"] = float(df.loc[nxt, "open"]), nxt.date().isoformat()
                filled += 1
            if "close_next" not in rec and final(nxt.date(), _CLOSE_FINAL):
                rec["close_next"] = float(df.loc[nxt, "close"])
                filled += 1

    if not failed:
        state["last_fill"] = today.isoformat()
    state["records"] = records
    store.save(state)
    return filled
