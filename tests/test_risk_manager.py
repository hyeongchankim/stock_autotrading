"""Sanity checks for stop-loss / take-profit logic.

Run with: pytest
(or: python -m unittest tests.test_risk_manager)
"""
from __future__ import annotations

import unittest
from datetime import date

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

    def test_to_dict_restore_round_trip_same_day(self):
        self.rm.roll_to_day(date(2026, 1, 2))
        self.rm.record_realized_pnl(-50_000)

        restored = RiskManager(seed_capital=1_000_000)
        restored.restore(self.rm.to_dict())

        self.assertEqual(restored.daily_realized_pnl, -50_000)
        self.assertFalse(restored.trading_halted_today)
        # restoring a same-day snapshot must survive the roll_to_day call
        # run_once always makes at the start of a cycle
        restored.roll_to_day(date(2026, 1, 2))
        self.assertEqual(restored.daily_realized_pnl, -50_000)

    def test_restore_stale_day_is_cleared_by_next_roll(self):
        self.rm.roll_to_day(date(2026, 1, 2))
        self.rm.record_realized_pnl(-300_001)  # halts trading that day

        restored = RiskManager(seed_capital=1_000_000)
        restored.restore(self.rm.to_dict())
        restored.roll_to_day(date(2026, 1, 3))  # a new day - counters must reset

        self.assertEqual(restored.daily_realized_pnl, 0.0)
        self.assertTrue(restored.can_open_new_position())

    def test_restore_empty_state_is_noop(self):
        self.rm.restore({})
        self.assertEqual(self.rm.daily_realized_pnl, 0.0)
        self.assertFalse(self.rm.trading_halted_today)


if __name__ == "__main__":
    unittest.main()
