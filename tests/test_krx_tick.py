"""Sanity checks for the KRX tick-size table.

Run with: pytest
(or: python -m unittest tests.test_krx_tick)
"""
from __future__ import annotations

import unittest

from broker.krx_tick import round_to_tick, tick_size


class TestTickSize(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(tick_size(1_500), 1)
        self.assertEqual(tick_size(3_000), 5)
        self.assertEqual(tick_size(10_000), 10)
        self.assertEqual(tick_size(30_000), 50)
        self.assertEqual(tick_size(100_000), 100)
        self.assertEqual(tick_size(300_000), 500)
        self.assertEqual(tick_size(1_000_000), 1_000)

    def test_tier_boundaries_use_the_higher_tier(self):
        # KRX tiers are defined as [lower, upper) - the boundary price
        # itself belongs to the tier above it.
        self.assertEqual(tick_size(2_000), 5)
        self.assertEqual(tick_size(5_000), 10)
        self.assertEqual(tick_size(50_000), 100)
        self.assertEqual(tick_size(500_000), 1_000)


class TestRoundToTick(unittest.TestCase):
    def test_rounds_to_nearest_valid_tick(self):
        self.assertEqual(round_to_tick(71_234), 71_200)  # 100-won tier
        self.assertEqual(round_to_tick(71_260), 71_300)
        self.assertEqual(round_to_tick(3_002), 3_000)  # 5-won tier
        self.assertEqual(round_to_tick(3_003), 3_005)

    def test_already_aligned_price_is_unchanged(self):
        self.assertEqual(round_to_tick(70_000), 70_000)

    def test_zero_or_negative_returns_zero(self):
        self.assertEqual(round_to_tick(0), 0)
        self.assertEqual(round_to_tick(-100), 0)


if __name__ == "__main__":
    unittest.main()
