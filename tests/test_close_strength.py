"""Sanity checks for the close-strength ('종가매매') strategy.

Run with: pytest
(or: python -m unittest tests.test_close_strength)
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from strategies.base import Signal
from strategies.close_strength import CloseStrengthStrategy


def _make_ohlcv(n: int = 25, base: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    closes = np.full(n, base)
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1000}, index=dates
    )


class TestCloseStrengthStrategy(unittest.TestCase):
    def test_holds_without_enough_history(self):
        ohlcv = _make_ohlcv(n=5)
        strategy = CloseStrengthStrategy(ma_period=20)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)

    def test_strong_close_above_ma_triggers_buy(self):
        ohlcv = _make_ohlcv(base=100.0)
        # last bar: closes near the day's high, well above the trailing MA
        ohlcv.loc[ohlcv.index[-1], ["open", "low", "high", "close"]] = [110, 108, 120, 119]
        strategy = CloseStrengthStrategy(ma_period=20, close_position_pct=0.8)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.BUY)

    def test_weak_close_below_ma_triggers_sell(self):
        ohlcv = _make_ohlcv(base=100.0)
        ohlcv.loc[ohlcv.index[-1], ["open", "low", "high", "close"]] = [90, 80, 92, 81]
        strategy = CloseStrengthStrategy(ma_period=20, close_position_pct=0.8)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.SELL)

    def test_indecisive_middle_close_holds(self):
        ohlcv = _make_ohlcv(base=100.0)
        # closes in the middle of the day's range - neither strong nor weak
        ohlcv.loc[ohlcv.index[-1], ["open", "low", "high", "close"]] = [100, 95, 105, 100]
        strategy = CloseStrengthStrategy(ma_period=20, close_position_pct=0.8)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)

    def test_zero_range_day_holds(self):
        ohlcv = _make_ohlcv(base=100.0)  # every bar has high == low
        strategy = CloseStrengthStrategy(ma_period=20)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)


if __name__ == "__main__":
    unittest.main()
