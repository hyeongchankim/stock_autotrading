"""Sanity checks for the Buy & Hold benchmark.

Run with: pytest
(or: python -m unittest tests.test_benchmark)
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from backtest.benchmark import run_buy_and_hold


def _make_ohlcv(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    closes = np.array(closes, dtype=float)
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1000},
        index=dates,
    )


class TestBuyAndHold(unittest.TestCase):
    def test_equal_weight_allocation_and_final_value(self):
        symbol_data = {
            "A": _make_ohlcv([100, 110, 120]),  # +20%
            "B": _make_ohlcv([100, 90, 80]),  # -20%
        }
        curve = run_buy_and_hold(symbol_data, seed_capital=1000, start_bar_index=0)
        self.assertEqual(len(curve), 3)
        # symmetric +20%/-20% on equal weights should net out close to flat
        final_equity = float(curve["equity"].iloc[-1])
        self.assertLess(abs(final_equity - 1000), 50)

    def test_commission_reduces_starting_position(self):
        symbol_data = {"A": _make_ohlcv([100, 100, 100])}
        curve_no_cost = run_buy_and_hold(symbol_data, seed_capital=1000, start_bar_index=0, commission_pct=0.0)
        curve_with_cost = run_buy_and_hold(symbol_data, seed_capital=1000, start_bar_index=0, commission_pct=1.0)
        self.assertGreaterEqual(
            float(curve_no_cost["equity"].iloc[0]), float(curve_with_cost["equity"].iloc[0])
        )

    def test_empty_symbol_data_raises(self):
        with self.assertRaises(ValueError):
            run_buy_and_hold({}, seed_capital=1000, start_bar_index=0)

    def test_slippage_reduces_starting_position(self):
        symbol_data = {"A": _make_ohlcv([100, 100, 100])}
        curve_no_slippage = run_buy_and_hold(symbol_data, seed_capital=1000, start_bar_index=0, slippage_pct=0.0)
        curve_with_slippage = run_buy_and_hold(symbol_data, seed_capital=1000, start_bar_index=0, slippage_pct=1.0)
        self.assertGreaterEqual(
            float(curve_no_slippage["equity"].iloc[0]), float(curve_with_slippage["equity"].iloc[0])
        )


if __name__ == "__main__":
    unittest.main()
