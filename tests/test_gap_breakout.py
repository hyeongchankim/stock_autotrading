"""backtest/gap_breakout.py: the four-block pattern fires exactly where constructed, each layer is a
strict subset of the previous, and no layer uses data after the signal day."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from backtest.github_strategies import build_panels
from backtest.gap_breakout import Rule, layers

RULE = Rule(block=5, volume_days=5)  # 5-day blocks keep the synthetic history short (4*5 = 20 days + the signal day)


def _constructed(gap: bool = True, volume_surge: bool = True, flat2_range: float = 0.02) -> dict:
    """25 days: 5 up, 5 flat, 5 down, 5 flat (box high 105), then day 20 gaps up out of the box on volume."""
    n = 5
    closes = (
        list(np.linspace(80, 100, n))          # days 0-4   up   (+25%)
        + [100.0, 100.5, 100.0, 100.5, 100.0]  # days 5-9   flat
        + list(np.linspace(98, 90, n))         # days 10-14 down (-8% close-to-close within the block)
        + [90.0, 90.5, 90.0, 90.5, 90.0]       # days 15-19 flat box
    )
    highs = [c * 1.002 for c in closes]
    highs[15:20] = [90 * (1 + flat2_range)] * 5  # box high controls how wide the last box is
    lows = [c * 0.998 for c in closes]
    opens = list(closes)
    vols = [100_000.0] * 20
    # signal day 20: opens above the box high and yesterday's high, volume 3x the weekly mean
    box_high = max(highs[15:20])
    sig_open = box_high * 1.03 if gap else closes[-1]
    closes.append(sig_open * 1.01); opens.append(sig_open); highs.append(sig_open * 1.02); lows.append(sig_open * 0.99)
    vols.append(300_000.0 if volume_surge else 100_000.0)
    idx = pd.bdate_range("2024-01-01", periods=21)
    # trend line: a long flat history before would be needed for SMA60; use SMA5 in the rule instead
    return build_panels({"S.KS": pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}, index=idx)})


class TestLayers(unittest.TestCase):
    RULE = Rule(block=5, volume_days=5, trend_sma=5, up=0.10, down=0.05, flat1=0.10, flat2=0.08)

    def test_the_constructed_setup_triggers_every_layer_on_the_signal_day_only(self):
        out = layers(_constructed(), self.RULE)
        for name, mask in out.items():
            self.assertTrue(bool(mask.iloc[-1, 0]), name)
            self.assertEqual(int(mask.iloc[:-1].to_numpy().sum()), 0, name)

    def test_without_the_gap_or_the_volume_nothing_fires(self):
        for kwargs in ({"gap": False}, {"volume_surge": False}):
            out = layers(_constructed(**kwargs), self.RULE)
            self.assertEqual(sum(int(m.to_numpy().sum()) for m in out.values()), 0, kwargs)

    def test_a_ragged_last_box_fails_the_flat_box_layer_but_not_layer_one(self):
        out = layers(_constructed(flat2_range=0.20), self.RULE)  # box wider than 8%
        names = list(out)
        self.assertTrue(bool(out[names[0]].iloc[-1, 0]))
        self.assertFalse(bool(out[names[1]].iloc[-1, 0]))
        self.assertFalse(bool(out[names[2]].iloc[-1, 0]))
        self.assertFalse(bool(out[names[3]].iloc[-1, 0]))

    def test_layers_are_nested_and_no_layer_reads_future_rows(self):
        rng = np.random.default_rng(3)
        t, k = 160, 12
        idx = pd.bdate_range("2023-01-01", periods=t)
        data = {}
        for i in range(k):
            close = 20_000 * np.exp(np.cumsum(rng.normal(0, 0.03, t)))
            open_ = close * (1 + rng.normal(0, 0.03, t))
            data[f"{i:06d}.KS"] = pd.DataFrame({
                "open": open_, "high": np.maximum(open_, close) * 1.01, "low": np.minimum(open_, close) * 0.99,
                "close": close, "volume": rng.integers(1_000_000, 3_000_000, t) * (1 + 2 * (rng.random(t) < 0.1)),
            }, index=idx)
        loose = Rule(block=5, flat1=0.5, flat2=0.5, up=0.0, down=0.0, gap_min=0.0, trend_sma=20, volume_mult=1.0)
        full = layers(build_panels(data), loose)
        l1, l2, l2b, l3 = (m.to_numpy() for m in full.values())
        self.assertTrue((l2 <= l1).all() and (l2b <= l2).all() and (l3 <= l2b).all())  # strict nesting
        self.assertGreater(int(l2.sum()), 0)                       # not vacuous
        for i in (60, 100, 140):
            cut = layers(build_panels({s: d.iloc[: i + 1] for s, d in data.items()}), loose)
            for name in full:
                pd.testing.assert_series_equal(cut[name].iloc[i], full[name].iloc[i], check_names=False)


if __name__ == "__main__":
    unittest.main()
