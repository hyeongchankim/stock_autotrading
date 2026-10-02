"""backtest/overnight.py: return arithmetic and compounding."""
from __future__ import annotations

import unittest

import pandas as pd

from backtest.overnight import portfolio, trade_returns


def _panels():
    idx = pd.bdate_range("2024-01-01", periods=7)
    return {
        "close": pd.DataFrame({"A": [100.0, 110, 100, 100, 100, 100, 100]}, idx),
        "open": pd.DataFrame({"A": [100.0, 111, 99, 100, 100, 100, 100]}, idx),
    }


class TestTradeReturns(unittest.TestCase):
    def test_next_open_and_next_close_net_of_cost(self):
        nets = trade_returns(_panels(), round_trip_cost=0.002)
        # buy day-0 close 100, next open 111, next close 110
        self.assertAlmostEqual(nets["next open"].iloc[0, 0], 0.11 - 0.002)
        self.assertAlmostEqual(nets["next close"].iloc[0, 0], 0.10 - 0.002)
        # the part a next-open exit avoids: open 111 -> close 110
        self.assertAlmostEqual(nets["_intraday_next"].iloc[0, 0], 110 / 111 - 1)
        self.assertTrue(pd.isna(nets["next open"].iloc[-1, 0]))  # no tomorrow -> not a trade


class TestPortfolio(unittest.TestCase):
    def test_compounds_weighted_returns_and_skips_unavailable_days(self):
        idx = pd.bdate_range("2024-01-01", periods=4)
        entries = pd.DataFrame({"A": [True, True, True, True]}, idx)
        net = pd.DataFrame({"A": [0.10, -0.10, 0.10, float("nan")]}, idx)  # last day has no exit price
        res = portfolio(entries, net, weight=0.5, start=0)
        self.assertEqual(res["trades"], 3)
        self.assertAlmostEqual(res["return%"], (1.05 * 0.95 * 1.05 - 1) * 100)
        self.assertAlmostEqual(res["win%"], 2 / 3 * 100)


if __name__ == "__main__":
    unittest.main()
