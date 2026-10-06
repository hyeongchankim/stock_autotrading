"""backtest/github_strategies.py: the re-coded rules of three public strategies -
no look-ahead in A's selection, exact return arithmetic, and the signal definitions."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from backtest.github_strategies import (
    TIERS, build_panels, closing_bet_masks, mean_reversion, portfolio, volume_decline_signals,
)


def _random_panels(seed: int, n: int = 14, t: int = 90) -> dict:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=t)
    data = {}
    for k in range(n):
        close = 20_000 * np.exp(np.cumsum(rng.normal(0, 0.03, t)))
        open_ = close * (1 + rng.normal(0, 0.03, t))  # frequent gaps both ways
        data[f"{k:06d}.KS"] = pd.DataFrame(
            {"open": open_, "high": np.maximum(open_, close) * 1.01, "low": np.minimum(open_, close) * 0.99,
             "close": close, "volume": rng.integers(2_000_000, 4_000_000, t).astype(float)}, index=idx)
    return build_panels(data)


class TestMeanReversion(unittest.TestCase):
    TIER = TIERS["small-tier rules (loose)"]

    def test_selection_never_uses_future_rows(self):
        full = _random_panels(1)
        sel_full, _, _ = mean_reversion(full, self.TIER, cost=0.0)
        self.assertGreater(int(sel_full.to_numpy().sum()), 0)  # not a vacuous pass
        for i in (30, 50, 70):
            cut = {k: v.iloc[: i + 1] for k, v in full.items()}
            sel_cut, _, _ = mean_reversion(cut, self.TIER, cost=0.0)
            pd.testing.assert_series_equal(sel_cut.iloc[i], sel_full.iloc[i], check_names=False)

    def test_return_is_next_open_over_open_net_of_cost_with_the_close_stop(self):
        idx = pd.bdate_range("2024-01-01", periods=4)
        base = dict(high=1.0, low=1.0, volume=1_000_000.0)

        def frame(opens, closes):
            return pd.DataFrame({"open": opens, **{k: [x] * 4 for k, x in base.items()}, "close": closes}, index=idx)

        # A: gaps down 5% on day 2 (open 95), next open 98; close 96 is NOT below 95*0.98 -> exit at next open
        # B: same entry but the day closes at 92 (< 93.1) -> close-stop, exit at the close
        data = {"A.KS": frame([100, 95, 98, 98], [100, 96, 98, 98]), "B.KS": frame([100, 95, 98, 98], [100, 92, 98, 98])}
        p = build_panels(data)
        _, net, _ = mean_reversion(p, self.TIER, cost=0.002)
        self.assertAlmostEqual(net.loc[idx[1], "A.KS"], 98 / 95 - 1 - 0.002)
        self.assertAlmostEqual(net.loc[idx[1], "B.KS"], 92 / 95 - 1 - 0.002)

    def test_portfolio_caps_weight_and_compounds(self):
        idx = pd.bdate_range("2024-01-01", periods=3)
        sel = pd.DataFrame({"X": [True, True, False], "Y": [False, True, False]}, idx)
        net = pd.DataFrame({"X": [0.10, 0.10, 0.0], "Y": [0.0, -0.10, 0.0]}, idx)
        res = portfolio(sel, net, start=0, max_weight=0.5)
        # day0: one pick -> weight capped at 0.5 -> +5%; day1: two picks -> 0.5 each -> 0.5*0.1 + 0.5*-0.1 = 0
        self.assertAlmostEqual(res["return%"], 5.0)
        self.assertEqual(res["trades"], 3)


class TestVolumePattern(unittest.TestCase):
    def test_each_decline_run_length_is_a_separate_causal_signal(self):
        idx = pd.bdate_range("2024-01-01", periods=30)
        vol = [v * 100 for v in [100.0] * 22 + [500.0, 700.0, 600.0, 500.0, 400.0, 400.0, 100.0, 100.0]]  # spike, higher, 3 declines, then flat (a rebound would itself be a new inflow)
        p = build_panels({"V.KS": pd.DataFrame(
            {"open": 1.0, "high": 1.0, "low": 1.0, "close": 10_000.0, "volume": vol}, index=idx)})
        sig = volume_decline_signals(p, multiplier=2.0, min_value=1e6)
        day = lambda k: idx[k]
        self.assertTrue(sig["1 declining day"].loc[day(24), "V.KS"])   # 600 < 700
        self.assertTrue(sig["2 declining days"].loc[day(25), "V.KS"])  # 500 < 600
        self.assertTrue(sig["3 declining days"].loc[day(26), "V.KS"])  # 400 < 500
        self.assertEqual(int(sum(s.to_numpy().sum() for s in sig.values())), 3)  # nothing on the flat day or elsewhere

    def test_a_weak_spike_below_the_multiplier_never_starts_the_pattern(self):
        idx = pd.bdate_range("2024-01-01", periods=30)
        vol = [v * 100 for v in [100.0] * 22 + [150.0, 200.0, 190.0, 180.0, 170.0, 100.0, 100.0, 100.0]]  # 1.5x < 2x
        p = build_panels({"V.KS": pd.DataFrame(
            {"open": 1.0, "high": 1.0, "low": 1.0, "close": 10_000.0, "volume": vol}, index=idx)})
        self.assertEqual(int(sum(s.to_numpy().sum() for s in volume_decline_signals(p, 2.0, 1e6).values())), 0)


class TestClosingBet(unittest.TestCase):
    def test_a_textbook_bar_passes_every_layer_and_flat_days_pass_none(self):
        t = 80
        idx = pd.bdate_range("2024-01-01", periods=t)
        o, h, l, c, v = ([10_000.0] * t for _ in range(5))
        v = [1_000_000.0] * t
        o[-1], h[-1], l[-1], c[-1], v[-1] = 10_000.0, 11_500.0, 10_000.0, 11_400.0, 6_000_000.0  # +14%, strong close
        p = build_panels({"K.KS": pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=idx)})
        masks = closing_bet_masks(p)
        for name, m in masks.items():
            self.assertTrue(bool(m.iloc[-1, 0]), name)
            self.assertEqual(int(m.iloc[:-1].to_numpy().sum()), 0, name)  # flat history triggers nothing

    def test_a_weak_close_fails_the_closing_strength_rule(self):
        t = 80
        idx = pd.bdate_range("2024-01-01", periods=t)
        o, h, l, c = ([10_000.0] * t for _ in range(4))
        v = [1_000_000.0] * t
        o[-1], h[-1], l[-1], c[-1], v[-1] = 10_000.0, 12_400.0, 10_000.0, 11_400.0, 6_000_000.0  # closes at 92% of the high
        p = build_panels({"K.KS": pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=idx)})
        self.assertFalse(bool(closing_bet_masks(p)["5 core conditions"].iloc[-1, 0]))


if __name__ == "__main__":
    unittest.main()
