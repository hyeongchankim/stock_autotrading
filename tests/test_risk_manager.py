"""Sanity checks for stop-loss / take-profit logic.

Run with: pytest
(or: python -m unittest tests.test_risk_manager)
"""
from __future__ import annotations

import unittest

from risk.risk_manager import RiskManager


class TestRiskManager(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(
            seed_capital=1_000_000,
            stop_loss_pct=0.10,
            take_profit_pct=0.07,
            position_size_pct=0.30,
            daily_max_loss_pct=0.30,
        )

    def test_stop_loss_triggers(self):
        trigger = self.rm.check_exit_trigger("TEST", avg_entry_price=100, current_price=89)
        self.assertEqual(trigger, "STOP_LOSS")

    def test_take_profit_triggers(self):
        trigger = self.rm.check_exit_trigger("TEST", avg_entry_price=100, current_price=108)
        self.assertEqual(trigger, "TAKE_PROFIT")

    def test_no_exit_inside_thresholds(self):
        trigger = self.rm.check_exit_trigger("TEST", avg_entry_price=100, current_price=103)
        self.assertIsNone(trigger)

    def test_daily_loss_limit_halts_new_entries(self):
        self.rm.roll_to_day(__import__("datetime").date(2026, 1, 2))
        self.assertTrue(self.rm.can_open_new_position())
        self.rm.record_realized_pnl(-300_001)  # 30% of 1,000,000 seed
        self.assertFalse(self.rm.can_open_new_position())


if __name__ == "__main__":
    unittest.main()
