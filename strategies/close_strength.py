"""'종가 매매' - trades on end-of-day closing strength rather than an
intraday indicator crossing a threshold every day. Deliberately low-
frequency: RSI/volatility-breakout react to any wiggle inside a single bar,
this only fires when a bar's own close confirms conviction (near the day's
high, not just above it) and the trend agrees - fewer, higher-conviction
entries by design, aimed at avoiding frequent stop-outs rather than
maximizing trade count.
"""
from __future__ import annotations

import pandas as pd

from strategies.base import Signal, Strategy, StrategyCategory, StrategySignal


class CloseStrengthStrategy(Strategy):
    """BUY: today's close sits in the top close_position_pct of the day's own
    high-low range (buyers were in control into the close, not just an
    intraday spike that faded) AND the close is above the trailing moving
    average (confirms this agrees with the existing trend, not a one-day
    bounce against it).

    SELL: the mirror case - a weak close (bottom of the day's range) below
    the moving average.
    """

    name = "close_strength"
    category = StrategyCategory.TREND_FOLLOWING

    def __init__(self, ma_period: int = 20, close_position_pct: float = 0.8):
        self.ma_period = ma_period
        self.close_position_pct = close_position_pct

    def min_bars_required(self) -> int:
        return self.ma_period + 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        if len(ohlcv) < self.min_bars_required():
            return self._hold(symbol, "insufficient history")

        row = ohlcv.iloc[-1]
        day_range = float(row["high"] - row["low"])
        price = float(row["close"])
        if day_range <= 0:
            return self._hold(symbol, "no intraday range today")

        close_position = (price - float(row["low"])) / day_range
        ma = float(ohlcv["close"].iloc[-self.ma_period :].mean())

        if close_position >= self.close_position_pct and price > ma:
            return StrategySignal(
                symbol, Signal.BUY, self.name,
                f"strong close ({close_position:.0%} of day's range, above {self.ma_period}MA)", price,
            )
        if close_position <= (1 - self.close_position_pct) and price < ma:
            return StrategySignal(
                symbol, Signal.SELL, self.name,
                f"weak close ({close_position:.0%} of day's range, below {self.ma_period}MA)", price,
            )
        return self._hold(symbol, f"close at {close_position:.0%} of range - not decisive")
