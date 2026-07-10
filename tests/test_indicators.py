"""Sanity checks for indicator math and strategy signal generation.

Run with: pytest
(or: python -m unittest tests.test_indicators)
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from strategies.base import Signal, StrategyCategory
from strategies.mean_reversion import RSIStrategy, VolatilityBreakoutStrategy
from strategies.regime import RegimeFilter
from strategies.trend_following import (
    BollingerBreakoutStrategy,
    DonchianBreakoutStrategy,
    MACDStrategy,
    MovingAverageCrossStrategy,
)
from strategies.volume_filter import VolumeFilter
from utils.indicators import adx, bollinger_bands, macd, rsi, sma


def _make_ohlcv(closes: list[float]) -> pd.DataFrame:
    closes = np.array(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": np.full(len(closes), 1000),
        }
    )


class TestIndicators(unittest.TestCase):
    def test_sma_basic(self):
        series = pd.Series([1, 2, 3, 4, 5])
        result = sma(series, window=2)
        self.assertTrue(np.isnan(result.iloc[0]))
        self.assertAlmostEqual(result.iloc[-1], 4.5)

    def test_bollinger_bands_ordering(self):
        series = pd.Series(np.linspace(100, 120, 40))
        upper, mid, lower = bollinger_bands(series, window=20, num_std=2)
        self.assertTrue((upper.dropna() >= mid.dropna()).all())
        self.assertTrue((mid.dropna() >= lower.dropna()).all())

    def test_rsi_bounds(self):
        series = pd.Series(np.linspace(100, 150, 30))
        result = rsi(series, period=14).dropna()
        self.assertTrue((result >= 0).all() and (result <= 100).all())

    def test_adx_bounds_and_trend_detection(self):
        trending = _make_ohlcv(list(np.linspace(100, 160, 40)))
        result = adx(trending, period=14).dropna()
        self.assertTrue((result >= 0).all() and (result <= 100).all())
        # a clean, steady uptrend should register as a strong trend
        self.assertGreater(float(result.iloc[-1]), 20.0)

    def test_macd_line_above_signal_in_uptrend(self):
        series = pd.Series(np.linspace(100, 150, 60))
        macd_line, signal_line, hist = macd(series, fast=12, slow=26, signal=9)
        # in a steady uptrend the MACD line should sit above its signal line
        self.assertGreater(float(macd_line.iloc[-1]), float(signal_line.iloc[-1]))
        self.assertAlmostEqual(float(hist.iloc[-1]), float(macd_line.iloc[-1] - signal_line.iloc[-1]))


class TestRegimeFilter(unittest.TestCase):
    def test_insufficient_history_returns_none(self):
        ohlcv = _make_ohlcv([100] * 10)
        regime = RegimeFilter(adx_period=14)
        self.assertIsNone(regime.detect(ohlcv))

    def test_strong_trend_selects_trend_following(self):
        ohlcv = _make_ohlcv(list(np.linspace(100, 200, 60)))
        regime = RegimeFilter(adx_period=14, trend_threshold=25.0, range_threshold=20.0)
        self.assertEqual(regime.detect(ohlcv), StrategyCategory.TREND_FOLLOWING)

    def test_rejects_invalid_thresholds(self):
        with self.assertRaises(ValueError):
            RegimeFilter(trend_threshold=10.0, range_threshold=20.0)


class TestStrategies(unittest.TestCase):
    def test_ma_cross_golden_cross_triggers_buy(self):
        closes = [100] * 20 + [101, 103, 106, 110, 115, 120]
        ohlcv = _make_ohlcv(closes)
        strategy = MovingAverageCrossStrategy(short_window=3, long_window=10)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertIn(result.signal, (Signal.BUY, Signal.HOLD))

    def test_rsi_extreme_oversold_triggers_buy(self):
        closes = list(np.linspace(150, 100, 30))
        ohlcv = _make_ohlcv(closes)
        strategy = RSIStrategy(period=14, oversold=30, overbought=70)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.BUY)

    def test_bollinger_breakout_requires_enough_bars(self):
        ohlcv = _make_ohlcv([100] * 5)
        strategy = BollingerBreakoutStrategy(window=20)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)

    def test_volatility_breakout_requires_enough_bars(self):
        ohlcv = _make_ohlcv([100])
        strategy = VolatilityBreakoutStrategy(k=0.5)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)

    def test_donchian_breakout_triggers_buy_on_new_high(self):
        closes = [100] * 20 + [130]  # 130 clears the prior 20-day high of 100
        ohlcv = _make_ohlcv(closes)
        strategy = DonchianBreakoutStrategy(entry_window=20, exit_window=10)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.BUY)

    def test_donchian_breakout_requires_enough_bars(self):
        ohlcv = _make_ohlcv([100] * 5)
        strategy = DonchianBreakoutStrategy(entry_window=20, exit_window=10)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)

    def test_macd_requires_enough_bars(self):
        ohlcv = _make_ohlcv([100] * 5)
        strategy = MACDStrategy()
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)

    def test_macd_rejects_fast_not_smaller_than_slow(self):
        with self.assertRaises(ValueError):
            MACDStrategy(fast=26, slow=12)


class TestVolumeFilter(unittest.TestCase):
    def test_insufficient_history_does_not_block(self):
        ohlcv = _make_ohlcv([100] * 5)
        vf = VolumeFilter(window=20, multiplier=1.5)
        self.assertTrue(vf.confirms(ohlcv))

    def test_volume_spike_confirms(self):
        ohlcv = _make_ohlcv([100] * 25)
        ohlcv.loc[ohlcv.index[-1], "volume"] = 5000  # 5x the 1000 average
        vf = VolumeFilter(window=20, multiplier=1.5)
        self.assertTrue(vf.confirms(ohlcv))

    def test_flat_volume_does_not_confirm(self):
        ohlcv = _make_ohlcv([100] * 25)  # constant 1000 volume, no spike
        vf = VolumeFilter(window=20, multiplier=1.5)
        self.assertFalse(vf.confirms(ohlcv))


if __name__ == "__main__":
    unittest.main()
