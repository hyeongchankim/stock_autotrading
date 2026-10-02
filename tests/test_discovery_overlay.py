"""Checks for backtest/discovery_overlay.py - the vectorised pattern flags must
agree with the strategy classes they stand in for, and candidate selection
must apply the same filters/ranking as screening/discover.py.
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from backtest.discovery_overlay import PATTERN_STRATEGIES, candidate_mask, pattern_flags
from strategies.base import Signal


def _random_ohlcv(seed: int, bars: int = 420) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10_000 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, bars)))
    volume = rng.integers(100_000, 600_000, bars).astype(float)
    volume[rng.random(bars) < 0.05] *= 4  # some volume spikes so the 52w-high rule can fire
    idx = pd.bdate_range("2020-01-01", periods=bars)
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": volume}, index=idx
    )


class TestPatternFlagsMatchStrategies(unittest.TestCase):
    def test_flags_equal_generate_signal_on_every_prefix(self):
        fired = {name: 0 for name in PATTERN_STRATEGIES}
        for seed in range(6):
            df = _random_ohlcv(seed)
            flags = pattern_flags(df)
            for name, make in PATTERN_STRATEGIES.items():
                strategy = make()
                for i in range(len(df)):
                    expected = strategy.generate_signal("X", df.iloc[: i + 1]).signal == Signal.BUY
                    self.assertEqual(bool(flags[name].iloc[i]), expected, f"{name} seed={seed} bar={i}")
                    fired[name] += expected
        # a vacuous pass (flag never True) would prove nothing
        for name in ("golden_cross", "macd_cross", "stacked_ma"):
            self.assertGreater(fired[name], 0, name)


class TestCandidateMask(unittest.TestCase):
    CFG = {"min_price": 5000, "max_change_pct": 15.0, "min_trading_value": 3e9, "max_candidates": 2}

    def _panels(self):
        idx = pd.bdate_range("2024-01-01", periods=1)
        cols = ["A", "B", "C", "D", "E"]
        return {
            "close": pd.DataFrame([[10_000, 10_000, 10_000, 4_000, 10_000]], idx, columns=cols),
            "volume": pd.DataFrame([[1e6, 1e6, 1e6, 1e6, 1e3]], idx, columns=cols),  # E: too illiquid
            "chg": pd.DataFrame([[5.0, 9.0, 20.0, 12.0, 11.0]], idx, columns=cols),  # C: over +15%
            "golden_cross": pd.DataFrame([[True, False, True, True, True]], idx, columns=cols),
        }

    def test_baseline_applies_filters_then_takes_top_n_by_change(self):
        mask = candidate_mask(self._panels(), self.CFG, None, "-").iloc[0]
        # C (>15%), D (price<5000), E (illiquid) are filtered; A and B remain
        self.assertEqual(list(mask[mask].index), ["A", "B"])

    def test_top_n_then_pattern_can_shrink_the_list(self):
        mask = candidate_mask(self._panels(), self.CFG, "golden_cross", "top5_then_pattern").iloc[0]
        self.assertEqual(list(mask[mask].index), ["A"])  # B was in the top-N but lacks the pattern

    def test_pattern_then_top_n_refills_from_the_pattern_matches(self):
        panels = self._panels()
        panels["chg"].iloc[0] = [5.0, 9.0, 3.0, 2.0, 1.0]
        panels["close"].iloc[0] = 10_000
        panels["volume"].iloc[0] = 1e6
        mask = candidate_mask(panels, self.CFG, "golden_cross", "pattern_then_top5").iloc[0]
        # pattern holders A,C,D,E ranked by change -> A(5), C(3) are the top 2; B (no pattern) never competes
        self.assertEqual(list(mask[mask].index), ["A", "C"])


if __name__ == "__main__":
    unittest.main()
