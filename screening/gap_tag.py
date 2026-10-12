"""L1 tag for the forward test: "volume surge + gap-up above the trend line" (backtest/gap_breakout.py, layer L1).

  volume_today >= 2.0 x mean(volume of the previous 5 days)
  AND open_today > high_yesterday AND open_today / close_yesterday - 1 >= 1%
  AND open_today > SMA60 of closes as of yesterday

Backtest on 347 names, 2019-2026 (net of costs): buying the signal day's CLOSE and selling the NEXT OPEN made
+0.34% per trade (t=6.6) - but that used the day's final volume and close. This module measures the same rule the way
it would actually be traded, by tagging each logged 15:20 candidate twice:
  scan_tag  at 15:20, from KIS's still-open daily bar (today's volume so far - slightly short of the final figure,
            so volume-ratio is understated: the honest, tradable view);
  eod_tag   next time the log is filled, from the finished bar (the backtest's own definition).
screening/forward_report.py then compares the overnight outcome of tagged vs untagged candidates.
Observation only - nothing here places an order.
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

logger = logging.getLogger("screening.gap_tag")

VOLUME_MULT = 2.0
VOLUME_DAYS = 5
TREND_SMA = 60
GAP_MIN = 0.01
MIN_BARS = TREND_SMA + 1  # SMA60 as of yesterday needs 60 prior closes, plus today


def l1_features(bars: pd.DataFrame, day: pd.Timestamp) -> dict | None:
    """The L1 conditions for `day` from daily bars (columns open/high/close/volume, ending at or after `day`).
    None when `day` has no bar or there is too little history to judge."""
    if day not in bars.index:
        return None
    i = bars.index.get_loc(day)
    if i < TREND_SMA or i < VOLUME_DAYS:
        return None
    o, c, h, v = (bars[k].to_numpy(dtype=float) for k in ("open", "close", "high", "volume"))
    weekly = v[i - VOLUME_DAYS:i].mean()
    vol_ratio = float(v[i] / weekly) if weekly > 0 else float("nan")
    trend = c[i - TREND_SMA:i].mean()
    gap = o[i] / c[i - 1] - 1
    over_prev_high = bool(o[i] > h[i - 1])
    above_trend = bool(o[i] > trend)
    return {
        "l1": bool(vol_ratio >= VOLUME_MULT and over_prev_high and gap >= GAP_MIN and above_trend),
        "vol_ratio": None if np.isnan(vol_ratio) else round(vol_ratio, 2),
        "gap_pct": round(float(gap) * 100, 2),
        "over_prev_high": over_prev_high,
        "above_trend": above_trend,
    }


def tag_scan(feed, symbols: list[str], as_of: date, lookback: int = 70) -> dict[str, dict]:
    """15:20 tags {symbol: features} from `feed`'s current daily bars. A symbol whose newest bar isn't `as_of`'s
    (halted, holiday) or whose data fails is simply left out - tagging never raises."""
    tags: dict[str, dict] = {}
    for symbol in symbols:
        try:
            bars = feed.get_ohlcv(symbol, "1d", lookback)
            if bars.empty or bars.index[-1].date() != as_of:
                continue
            features = l1_features(bars, bars.index[-1])
        except Exception as exc:  # noqa: BLE001 - observation must never break trading
            logger.warning("gap tag: skipped %s (%s)", symbol, exc)
            continue
        if features is not None:
            tags[symbol] = features
    return tags


def tag_eod(records: list[dict], get_bars, is_final) -> int:
    """Adds `eod_tag` (the backtest's own full-day definition) to logged rows that lack it, once the signal day's bar
    is final. `get_bars(symbol)` -> DataFrame | None; `is_final(date)` -> bool. A row with too little history gets
    {"l1": None} so it isn't fetched again. Returns the number of rows tagged."""
    tagged = 0
    for rec in records:
        if "eod_tag" in rec:
            continue
        day = pd.Timestamp(rec["date"])
        if not is_final(day.date()):
            continue
        bars = get_bars(rec["symbol"])
        if bars is None or bars.empty or day not in bars.index:
            continue
        rec["eod_tag"] = l1_features(bars, day) or {"l1": None}
        tagged += 1
    return tagged
