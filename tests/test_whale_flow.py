"""Sanity checks for the whale (institutional/foreign) flow strategy and its
data-merge helper. Uses synthetic data throughout - no KRX login needed.

Run with: pytest
(or: python -m unittest tests.test_whale_flow)
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from data.krx_investor_feed import attach_investor_flow
from strategies.base import Signal
from strategies.whale_flow import WhaleFlowStrategy


def _make_ohlcv(closes: list[float], tz: str | None = "Asia/Seoul") -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=len(closes), freq="D", tz=tz)
    closes = np.array(closes, dtype=float)
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": np.full(len(closes), 1000)},
        index=dates,
    )


class TestAttachInvestorFlow(unittest.TestCase):
    def test_merges_by_date_across_tz_aware_and_naive_index(self):
        ohlcv = _make_ohlcv([100, 101, 102], tz="Asia/Seoul")
        flow = pd.DataFrame(
            {"institutional_net": [1000, 2000, 3000], "foreign_net": [500, -500, 1500]},
            index=pd.date_range("2024-01-02", periods=3, freq="D"),
        )
        merged = attach_investor_flow(ohlcv, flow)
        self.assertIn("institutional_net", merged.columns)
        self.assertEqual(list(merged["institutional_net"]), [1000, 2000, 3000])
        self.assertEqual(list(merged["foreign_net"]), [500, -500, 1500])

    def test_missing_dates_become_nan(self):
        ohlcv = _make_ohlcv([100, 101, 102])
        flow = pd.DataFrame(
            {"institutional_net": [1000], "foreign_net": [500]},
            index=pd.date_range("2024-01-02", periods=1, freq="D"),
        )
        merged = attach_investor_flow(ohlcv, flow)
        self.assertTrue(pd.isna(merged["institutional_net"].iloc[-1]))


class TestWhaleFlowStrategy(unittest.TestCase):
    def test_holds_without_investor_flow_columns(self):
        ohlcv = _make_ohlcv([100] * 10)
        strategy = WhaleFlowStrategy(window=5)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)

    def test_strong_net_buying_triggers_buy(self):
        ohlcv = _make_ohlcv([100] * 10)
        # heavy sustained institutional+foreign buying relative to turnover
        ohlcv["institutional_net"] = 50_000
        ohlcv["foreign_net"] = 50_000
        strategy = WhaleFlowStrategy(window=5, buy_threshold_ratio=0.05, sell_threshold_ratio=0.05)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.BUY)

    def test_strong_net_selling_triggers_sell(self):
        ohlcv = _make_ohlcv([100] * 10)
        ohlcv["institutional_net"] = -50_000
        ohlcv["foreign_net"] = -50_000
        strategy = WhaleFlowStrategy(window=5, buy_threshold_ratio=0.05, sell_threshold_ratio=0.05)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.SELL)

    def test_todays_unconfirmed_nan_row_does_not_block_signal(self):
        # institutional/foreign net-buy data is confirmed by KRX with a lag,
        # so today's row is reliably NaN during market hours - the window
        # must anchor on the last CONFIRMED rows instead of holding just
        # because the most recent row hasn't settled yet.
        ohlcv = _make_ohlcv([100] * 10)
        ohlcv["institutional_net"] = 50_000
        ohlcv["foreign_net"] = 50_000
        ohlcv.loc[ohlcv.index[-1], "institutional_net"] = np.nan
        ohlcv.loc[ohlcv.index[-1], "foreign_net"] = np.nan
        strategy = WhaleFlowStrategy(window=5, buy_threshold_ratio=0.05, sell_threshold_ratio=0.05)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.BUY)

    def test_holds_when_confirmed_rows_fall_short_of_window(self):
        # two trailing rows unconfirmed (e.g. today plus a reporting gap)
        # leaves only 4 confirmed rows for a 5-day window - not enough,
        # unlike the single-trailing-NaN case above.
        ohlcv = _make_ohlcv([100] * 6)
        ohlcv["institutional_net"] = 50_000
        ohlcv["foreign_net"] = 50_000
        ohlcv.loc[ohlcv.index[-2:], "institutional_net"] = np.nan
        ohlcv.loc[ohlcv.index[-2:], "foreign_net"] = np.nan
        strategy = WhaleFlowStrategy(window=5)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)

    def test_requires_enough_bars(self):
        ohlcv = _make_ohlcv([100] * 3)
        ohlcv["institutional_net"] = 50_000
        ohlcv["foreign_net"] = 50_000
        strategy = WhaleFlowStrategy(window=5)
        result = strategy.generate_signal("TEST", ohlcv)
        self.assertEqual(result.signal, Signal.HOLD)


if __name__ == "__main__":
    unittest.main()
