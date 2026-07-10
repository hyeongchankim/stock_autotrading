"""Trend-following strategies: MA golden/dead cross, Bollinger band breakout."""
from __future__ import annotations

import pandas as pd

from strategies.base import Signal, Strategy, StrategyCategory, StrategySignal
from utils.indicators import bollinger_bands, macd, sma


class MovingAverageCrossStrategy(Strategy):
    """Golden cross (short MA crosses above long MA) -> BUY.
    Dead cross (short MA crosses below long MA) -> SELL.
    """

    name = "ma_golden_dead_cross"
    category = StrategyCategory.TREND_FOLLOWING

    def __init__(self, short_window: int = 5, long_window: int = 20):
        if short_window >= long_window:
            raise ValueError("short_window must be smaller than long_window")
        self.short_window = short_window
        self.long_window = long_window

    def min_bars_required(self) -> int:
        return self.long_window + 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        if len(ohlcv) < self.min_bars_required():
            return self._hold(symbol, "insufficient history")

        close = ohlcv["close"]
        short_ma = sma(close, self.short_window)
        long_ma = sma(close, self.long_window)

        prev_short, prev_long = short_ma.iloc[-2], long_ma.iloc[-2]
        curr_short, curr_long = short_ma.iloc[-1], long_ma.iloc[-1]
        price = float(close.iloc[-1])

        if prev_short <= prev_long and curr_short > curr_long:
            return StrategySignal(symbol, Signal.BUY, self.name, "golden cross", price)
        if prev_short >= prev_long and curr_short < curr_long:
            return StrategySignal(symbol, Signal.SELL, self.name, "dead cross", price)
        return self._hold(symbol, "no cross")


class BollingerBreakoutStrategy(Strategy):
    """Close breaking above the upper band -> BUY (ride the breakout).
    Close falling back below the middle band -> SELL (trend exhausted).

    Exiting at the middle band rather than waiting for a full breakdown to
    the lower band locks in more of the gain from the breakout move.
    """

    name = "bollinger_breakout"
    category = StrategyCategory.TREND_FOLLOWING

    def __init__(self, window: int = 20, num_std: float = 2.0):
        self.window = window
        self.num_std = num_std

    def min_bars_required(self) -> int:
        return self.window + 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        if len(ohlcv) < self.min_bars_required():
            return self._hold(symbol, "insufficient history")

        close = ohlcv["close"]
        upper, mid, _lower = bollinger_bands(close, self.window, self.num_std)
        price = float(close.iloc[-1])
        prev_price = float(close.iloc[-2])

        if prev_price <= upper.iloc[-2] and price > upper.iloc[-1]:
            return StrategySignal(symbol, Signal.BUY, self.name, "upper band breakout", price)
        if prev_price >= mid.iloc[-2] and price < mid.iloc[-1]:
            return StrategySignal(symbol, Signal.SELL, self.name, "fell back below middle band", price)
        return self._hold(symbol, "inside bands")


class DonchianBreakoutStrategy(Strategy):
    """Classic Turtle-style channel breakout.

    New N-day high -> BUY. New M-day low -> SELL. Uses a shorter exit window
    than entry window (e.g. 20/10) so it locks in a trend reversal faster
    than it commits to a new one - the original Turtle system's asymmetry.
    """

    name = "donchian_breakout"
    category = StrategyCategory.TREND_FOLLOWING

    def __init__(self, entry_window: int = 20, exit_window: int = 10):
        self.entry_window = entry_window
        self.exit_window = exit_window

    def min_bars_required(self) -> int:
        return max(self.entry_window, self.exit_window) + 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        if len(ohlcv) < self.min_bars_required():
            return self._hold(symbol, "insufficient history")

        high = ohlcv["high"]
        low = ohlcv["low"]
        price = float(ohlcv["close"].iloc[-1])

        # exclude today's own bar from the channel so today's price is
        # compared against the prior N/M days, not against itself
        entry_high = float(high.iloc[-self.entry_window - 1 : -1].max())
        exit_low = float(low.iloc[-self.exit_window - 1 : -1].min())

        if price > entry_high:
            return StrategySignal(symbol, Signal.BUY, self.name, f"new {self.entry_window}-day high", price)
        if price < exit_low:
            return StrategySignal(symbol, Signal.SELL, self.name, f"new {self.exit_window}-day low", price)
        return self._hold(symbol, "inside channel")


class MACDStrategy(Strategy):
    """MACD line crossing above its signal line -> BUY.
    Crossing below -> SELL.
    """

    name = "macd_cross"
    category = StrategyCategory.TREND_FOLLOWING

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        if fast >= slow:
            raise ValueError("fast must be smaller than slow")
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def min_bars_required(self) -> int:
        return self.slow + self.signal + 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        if len(ohlcv) < self.min_bars_required():
            return self._hold(symbol, "insufficient history")

        close = ohlcv["close"]
        macd_line, signal_line, _hist = macd(close, self.fast, self.slow, self.signal)
        price = float(close.iloc[-1])

        prev_macd, prev_signal = macd_line.iloc[-2], signal_line.iloc[-2]
        curr_macd, curr_signal = macd_line.iloc[-1], signal_line.iloc[-1]

        if prev_macd <= prev_signal and curr_macd > curr_signal:
            return StrategySignal(symbol, Signal.BUY, self.name, "MACD bullish cross", price)
        if prev_macd >= prev_signal and curr_macd < curr_signal:
            return StrategySignal(symbol, Signal.SELL, self.name, "MACD bearish cross", price)
        return self._hold(symbol, "no cross")
