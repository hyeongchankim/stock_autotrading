"""Mean-reversion strategies: RSI overbought/oversold, volatility breakout."""
from __future__ import annotations

import pandas as pd

from strategies.base import Signal, Strategy, StrategyCategory, StrategySignal
from utils.indicators import rsi, volatility_breakout_target


class RSIStrategy(Strategy):
    """RSI at/under oversold -> BUY. RSI at/over overbought -> SELL."""

    name = "rsi_reversion"
    category = StrategyCategory.MEAN_REVERSION

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def min_bars_required(self) -> int:
        return self.period + 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        if len(ohlcv) < self.min_bars_required():
            return self._hold(symbol, "insufficient history")

        close = ohlcv["close"]
        rsi_series = rsi(close, self.period)
        current_rsi = float(rsi_series.iloc[-1])
        price = float(close.iloc[-1])

        if current_rsi <= self.oversold:
            return StrategySignal(symbol, Signal.BUY, self.name, f"RSI {current_rsi:.1f} oversold", price)
        if current_rsi >= self.overbought:
            return StrategySignal(symbol, Signal.SELL, self.name, f"RSI {current_rsi:.1f} overbought", price)
        return self._hold(symbol, f"RSI {current_rsi:.1f} neutral")


class VolatilityBreakoutStrategy(Strategy):
    """Larry Williams style volatility breakout.

    target = today's open + (yesterday's high - yesterday's low) * k
    If today's high reaches the target, treat it as a breakout entry.
    """

    name = "volatility_breakout"
    category = StrategyCategory.MEAN_REVERSION

    def __init__(self, k: float = 0.5):
        self.k = k

    def min_bars_required(self) -> int:
        return 2

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        if len(ohlcv) < self.min_bars_required():
            return self._hold(symbol, "insufficient history")

        target = volatility_breakout_target(ohlcv, self.k)
        today_target = float(target.iloc[-1])
        if pd.isna(today_target):
            return self._hold(symbol, "insufficient history")

        today_high = float(ohlcv["high"].iloc[-1])
        price = float(ohlcv["close"].iloc[-1])

        if today_high >= today_target:
            return StrategySignal(symbol, Signal.BUY, self.name, f"breakout above target {today_target:.2f}", price)
        return self._hold(symbol, "target not reached")
