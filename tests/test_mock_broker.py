"""Sanity checks for MockBroker's cost model, focused on slippage_pct.

Run with: pytest
(or: python -m unittest tests.test_mock_broker)
"""
from __future__ import annotations

import unittest

from broker.mock_broker import MockBroker
from strategies.base import Signal


class TestMockBrokerSlippage(unittest.TestCase):
    def test_default_slippage_is_zero_no_behavior_change(self):
        broker = MockBroker(seed_capital=100_000)
        broker.set_price_lookup({"A": 100.0})
        broker.place_order("A", Signal.BUY, 10, 100.0)
        # no commission/tax/slippage configured -> exact fill price
        self.assertAlmostEqual(broker.get_cash_balance(), 100_000 - 10 * 100.0)

    def test_slippage_makes_buys_more_expensive(self):
        broker = MockBroker(seed_capital=100_000, slippage_pct=1.0)
        broker.set_price_lookup({"A": 100.0})
        broker.place_order("A", Signal.BUY, 10, 100.0)
        # effective price = 100 * 1.01 = 101
        self.assertAlmostEqual(broker.get_cash_balance(), 100_000 - 10 * 101.0)

    def test_slippage_makes_sells_cheaper(self):
        broker = MockBroker(seed_capital=100_000, slippage_pct=1.0)
        broker.set_price_lookup({"A": 100.0})
        broker.place_order("A", Signal.BUY, 10, 100.0)
        cash_after_buy = broker.get_cash_balance()
        result = broker.place_order("A", Signal.SELL, 10, 100.0)
        # effective sell price = 100 * (1 - 0.01) = 99
        self.assertAlmostEqual(broker.get_cash_balance(), cash_after_buy + 10 * 99.0)
        self.assertAlmostEqual(result.realized_pnl, (99.0 - 101.0) * 10)

    def test_slippage_stacks_with_commission_and_tax(self):
        broker = MockBroker(seed_capital=100_000, commission_pct=1.0, sell_tax_pct=2.0, slippage_pct=1.0)
        broker.set_price_lookup({"A": 100.0})
        broker.place_order("A", Signal.BUY, 10, 100.0)
        result = broker.place_order("A", Signal.SELL, 10, 100.0)
        # sell effective price = 100 * (1 - 0.01 - 0.02 - 0.01) = 96
        buy_effective = 100.0 * (1 + 0.01 + 0.01)  # commission + slippage
        self.assertAlmostEqual(result.realized_pnl, (96.0 - buy_effective) * 10)


if __name__ == "__main__":
    unittest.main()
