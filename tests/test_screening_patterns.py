"""Sanity checks for the Finviz/TradingView-style screener pattern
strategies (RSI+uptrend, 52-week-high breakout, stacked MA / 정배열).

Run with: pytest
(or: python -m unittest tests.test_screening_patterns)
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from strategies.base import Signal
from strategies.screening_patterns import (
    FiftyTwoWeekHighBreakoutStrategy,
    RSIUptrendStrategy,
    StackedMAStrategy,
)


def _make_ohlcv(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    closes = np.array(closes, dtype=float)
    volumes = np.array(volumes, dtype=float) if volumes is not None else np.full(n, 1000.0)
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes}, index=dates
    )


class TestRSIUptrendStrategy(unittest.TestCase):
    def test_holds_without_enough_history(self):
        ohlcv = _make_ohlcv([100] * 50)
        strategy = RSIUptrendStrategy(trend_window=200)
        self.assertEqual(strategy.generate_signal("TEST", ohlcv).signal, Signal.HOLD)

    def test_oversold_above_trend_buys(self):
        # a long rise (keeps SMA200 well below the still-elevated price)
        # then a short sharp pullback (pushes the faster RSI < 30 without
        # dragging the price back under its own 200-day average)
        closes = list(np.linspace(80, 150, 220)) + list(np.linspace(148, 135, 6))
        ohlcv = _make_ohlcv(closes)
        strategy = RSIUptrendStrategy(trend_window=200)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.BUY)

    def test_oversold_below_trend_holds(self):
        # sustained downtrend - RSI goes oversold but price stays under SMA200
        closes = list(np.linspace(150, 50, 220)) + [45, 40, 35, 30, 28, 26, 25, 24]
        ohlcv = _make_ohlcv(closes)
        strategy = RSIUptrendStrategy(trend_window=200)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)


class TestFiftyTwoWeekHighBreakoutStrategy(unittest.TestCase):
    def test_holds_without_enough_history(self):
        ohlcv = _make_ohlcv([100] * 100)
        strategy = FiftyTwoWeekHighBreakoutStrategy(lookback_days=252)
        self.assertEqual(strategy.generate_signal("TEST", ohlcv).signal, Signal.HOLD)

    def test_near_high_with_volume_surge_buys(self):
        closes = [100.0] * 251 + [101.0]
        volumes = [1000.0] * 271 + [2000.0]  # last day's volume well above the 20d average
        # pad closes to match volumes length (271 history + today)
        closes = [100.0] * 271 + [101.0]
        ohlcv = _make_ohlcv(closes, volumes)
        strategy = FiftyTwoWeekHighBreakoutStrategy(lookback_days=252, breakout_pct=0.02, volume_mult=1.5)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.BUY)

    def test_near_high_without_volume_holds(self):
        closes = [100.0] * 271 + [101.0]
        volumes = [1000.0] * 272  # no volume surge
        ohlcv = _make_ohlcv(closes, volumes)
        strategy = FiftyTwoWeekHighBreakoutStrategy(lookback_days=252, breakout_pct=0.02, volume_mult=1.5)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)

    def test_far_from_high_holds(self):
        closes = [100.0] * 271 + [50.0]
        volumes = [1000.0] * 271 + [5000.0]
        ohlcv = _make_ohlcv(closes, volumes)
        strategy = FiftyTwoWeekHighBreakoutStrategy(lookback_days=252, breakout_pct=0.02, volume_mult=1.5)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)


class TestStackedMAStrategy(unittest.TestCase):
    def test_holds_without_enough_history(self):
        ohlcv = _make_ohlcv([100] * 100)
        strategy = StackedMAStrategy(short_window=20, mid_window=50, long_window=200)
        self.assertEqual(strategy.generate_signal("TEST", ohlcv).signal, Signal.HOLD)

    def test_rejects_invalid_window_order(self):
        with self.assertRaises(ValueError):
            StackedMAStrategy(short_window=50, mid_window=20, long_window=200)

    def test_steady_uptrend_is_stacked_buy(self):
        closes = list(np.linspace(50, 250, 220))
        ohlcv = _make_ohlcv(closes)
        strategy = StackedMAStrategy(short_window=20, mid_window=50, long_window=200)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.BUY)

    def test_steady_downtrend_is_stacked_sell(self):
        closes = list(np.linspace(250, 50, 220))
        ohlcv = _make_ohlcv(closes)
        strategy = StackedMAStrategy(short_window=20, mid_window=50, long_window=200)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.SELL)

    def test_sideways_market_holds(self):
        closes = [100.0 + (i % 5) for i in range(220)]
        ohlcv = _make_ohlcv(closes)
        strategy = StackedMAStrategy(short_window=20, mid_window=50, long_window=200)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)


if __name__ == "__main__":
    unittest.main()
