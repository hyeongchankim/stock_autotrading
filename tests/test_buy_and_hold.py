"""Sanity checks for the hybrid mode's paper/live Buy & Hold sleeve.

Run with: pytest
(or: python -m unittest tests.test_buy_and_hold)
"""
from __future__ import annotations

import unittest

from portfolio.buy_and_hold import BuyAndHoldSleeve


class TestBuyAndHoldSleeve(unittest.TestCase):
    def test_ensure_purchased_equal_weights_across_watchlist(self):
        sleeve = BuyAndHoldSleeve(seed_capital=1000.0, watchlist=["A", "B"])
        sleeve.ensure_purchased({"A": 100.0, "B": 200.0})

        self.assertTrue(sleeve.purchased)
        self.assertEqual(sleeve.shares, {"A": 5, "B": 2})  # 500/100=5, 500/200=2
        self.assertAlmostEqual(sleeve.cash, 1000.0 - 5 * 100.0 - 2 * 200.0)

    def test_ensure_purchased_is_a_one_shot(self):
        sleeve = BuyAndHoldSleeve(seed_capital=1000.0, watchlist=["A"])
        sleeve.ensure_purchased({"A": 100.0})
        sleeve.ensure_purchased({"A": 999.0})  # should be ignored - already purchased
        self.assertEqual(sleeve.shares, {"A": 10})

    def test_ensure_purchased_no_prices_defers(self):
        sleeve = BuyAndHoldSleeve(seed_capital=1000.0, watchlist=["A"])
        sleeve.ensure_purchased({})
        self.assertFalse(sleeve.purchased)

    def test_current_value_marks_to_market(self):
        sleeve = BuyAndHoldSleeve(seed_capital=1000.0, watchlist=["A"])
        sleeve.ensure_purchased({"A": 100.0})  # buys 10 shares, cash left = 0
        self.assertAlmostEqual(sleeve.current_value({"A": 150.0}), 10 * 150.0)

    def test_to_dict_restore_round_trip(self):
        sleeve = BuyAndHoldSleeve(seed_capital=1000.0, watchlist=["A"])
        sleeve.ensure_purchased({"A": 100.0})

        restored = BuyAndHoldSleeve(seed_capital=1000.0, watchlist=["A"])
        restored.restore(sleeve.to_dict())

        self.assertEqual(restored.purchased, sleeve.purchased)
        self.assertEqual(restored.shares, sleeve.shares)
        self.assertAlmostEqual(restored.cash, sleeve.cash)

    def test_restore_empty_state_is_noop(self):
        sleeve = BuyAndHoldSleeve(seed_capital=1000.0, watchlist=["A"])
        sleeve.restore({})
        self.assertFalse(sleeve.purchased)
        self.assertEqual(sleeve.cash, 1000.0)


if __name__ == "__main__":
    unittest.main()
