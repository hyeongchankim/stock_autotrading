"""Sanity checks for hybrid strategy/buy-and-hold seed splitting.

Run with: pytest
(or: python -m unittest tests.test_hybrid_allocation)
"""
from __future__ import annotations

import unittest

from main import strategy_allocation_pct


class TestStrategyAllocationPct(unittest.TestCase):
    def test_disabled_hybrid_uses_full_seed(self):
        config = {"hybrid": {"enabled": False, "strategy_allocation_pct": 0.5}}
        self.assertEqual(strategy_allocation_pct(config), 1.0)

    def test_missing_hybrid_section_uses_full_seed(self):
        self.assertEqual(strategy_allocation_pct({}), 1.0)

    def test_enabled_hybrid_uses_configured_split(self):
        config = {"hybrid": {"enabled": True, "strategy_allocation_pct": 0.5}}
        self.assertEqual(strategy_allocation_pct(config), 0.5)


if __name__ == "__main__":
    unittest.main()
