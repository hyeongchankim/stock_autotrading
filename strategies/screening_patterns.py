"""Finviz/TradingView-style screener patterns not already covered by an
existing strategy - golden cross (MovingAverageCrossStrategy) and MACD
crossover (MACDStrategy) already exist in strategies/trend_following.py and
are reused as-is for these. This module adds the three that weren't: RSI
oversold gated by a long-term uptrend, 52-week high breakout, and stacked
(정배열) moving averages.
"""
from __future__ import annotations

import pandas as pd

from strategies.base import Signal, Strategy, StrategyCategory, StrategySignal
from utils.indicators import rsi, sma


class RSIUptrendStrategy(Strategy):
    """RSI oversold dip-buy, but only when price is above the long-term
    trend (SMA200) - the plain RSIStrategy buys any oversold dip regardless
    of trend direction; this adds the trend gate from the reference table's
    "RSI Oversold + Uptrend" pattern.
    """

    name = "rsi_uptrend"
    category = StrategyCategory.MEAN_REVERSION

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0, trend_window: int = 200):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.trend_window = trend_window

    def min_bars_required(self) -> int:
        return max(self.period, self.trend_window) + 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        if len(ohlcv) < self.min_bars_required():
            return self._hold(symbol, "insufficient history")

        close = ohlcv["close"]
        current_rsi = float(rsi(close, self.period).iloc[-1])
        trend = float(sma(close, self.trend_window).iloc[-1])
        price = float(close.iloc[-1])

        if current_rsi <= self.oversold and price > trend:
            return StrategySignal(symbol, Signal.BUY, self.name, f"RSI {current_rsi:.1f} oversold above SMA{self.trend_window}", price)
        if current_rsi >= self.overbought:
            return StrategySignal(symbol, Signal.SELL, self.name, f"RSI {current_rsi:.1f} overbought", price)
        return self._hold(symbol, f"RSI {current_rsi:.1f} or trend condition not met")


class FiftyTwoWeekHighBreakoutStrategy(Strategy):
    """BUY when today's close is within breakout_pct of the trailing
    52-week (lookback_days) high AND today's volume exceeds volume_mult
    times the 20-day average volume - "강한 모멘텀 폭발". BUY-only, like
    VolatilityBreakoutStrategy - exits are left to stop-loss/take-profit/
    other strategies' SELL signals.
    """

    name = "fifty_two_week_high_breakout"
    category = StrategyCategory.TREND_FOLLOWING

    def __init__(self, lookback_days: int = 252, breakout_pct: float = 0.02, volume_mult: float = 1.5, volume_window: int = 20):
        self.lookback_days = lookback_days
        self.breakout_pct = breakout_pct
        self.volume_mult = volume_mult
        self.volume_window = volume_window

    def min_bars_required(self) -> int:
        return max(self.lookback_days, self.volume_window) + 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        if len(ohlcv) < self.min_bars_required():
            return self._hold(symbol, "insufficient history")

        price = float(ohlcv["close"].iloc[-1])
        high_52w = float(ohlcv["close"].iloc[-self.lookback_days :].max())
        avg_volume = float(ohlcv["volume"].iloc[-self.volume_window - 1 : -1].mean())
        today_volume = float(ohlcv["volume"].iloc[-1])

        near_high = price >= high_52w * (1 - self.breakout_pct)
        volume_confirmed = avg_volume > 0 and today_volume >= avg_volume * self.volume_mult

        if near_high and volume_confirmed:
            return StrategySignal(
                symbol, Signal.BUY, self.name,
                f"near 52w high ({price:.0f} vs {high_52w:.0f}), volume {today_volume/avg_volume:.1f}x avg", price,
            )
        return self._hold(symbol, "not a 52-week-high breakout")


class StackedMAStrategy(Strategy):
    """정배열(stacked uptrend): close > SMA_short > SMA_mid > SMA_long ->
    BUY. The mirror 역배열 (close < SMA_short < SMA_mid < SMA_long) -> SELL.
    """

    name = "stacked_ma"
    category = StrategyCategory.TREND_FOLLOWING

    def __init__(self, short_window: int = 20, mid_window: int = 50, long_window: int = 200):
        if not (short_window < mid_window < long_window):
            raise ValueError("short_window < mid_window < long_window required")
        self.short_window = short_window
        self.mid_window = mid_window
        self.long_window = long_window

    def min_bars_required(self) -> int:
        return self.long_window + 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        if len(ohlcv) < self.min_bars_required():
            return self._hold(symbol, "insufficient history")

        close = ohlcv["close"]
        price = float(close.iloc[-1])
        sma_short = float(sma(close, self.short_window).iloc[-1])
        sma_mid = float(sma(close, self.mid_window).iloc[-1])
        sma_long = float(sma(close, self.long_window).iloc[-1])

        if price > sma_short > sma_mid > sma_long:
            return StrategySignal(symbol, Signal.BUY, self.name, "정배열 (stacked uptrend)", price)
        if price < sma_short < sma_mid < sma_long:
            return StrategySignal(symbol, Signal.SELL, self.name, "역배열 (stacked downtrend)", price)
        return self._hold(symbol, "moving averages not stacked")
