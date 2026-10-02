"""backtest/early_setup.py: the vectorised signal flags must equal the live
strategy classes they stand in for (close_strength, VolumeFilter), and the
'recent' window must keep a one-day event alive for exactly `recent` bars.
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from backtest.early_setup import EVENT_SIGNALS, SIGNALS, SetupEntryStrategy, signal_flags, whale_series
from strategies.whale_flow import WhaleFlowStrategy
from strategies.base import Signal
from strategies.close_strength import CloseStrengthStrategy
from strategies.volume_filter import VolumeFilter
from tests.test_discovery_overlay import _random_ohlcv


def _with_real_ranges(seed: int) -> pd.DataFrame:
    df = _random_ohlcv(seed)
    rng = np.random.default_rng(seed + 100)
    df["high"] = df["close"] * (1 + rng.random(len(df)) * 0.03)
    df["low"] = df["close"] * (1 - rng.random(len(df)) * 0.03)
    return df


class TestSignalFlags(unittest.TestCase):
    def test_close_strength_and_volume_surge_match_the_live_classes(self):
        cs, vf = CloseStrengthStrategy(20, 0.8), VolumeFilter(20, 1.5)
        fired = {"close_strength": 0, "volume_surge": 0}
        for seed in range(4):
            df = _with_real_ranges(seed)
            flags = signal_flags(df, recent=3)
            for i in range(21, len(df)):
                window = df.iloc[: i + 1]
                is_cs = cs.generate_signal("X", window).signal == Signal.BUY
                is_surge = vf.confirms(window)
                self.assertEqual(bool(flags["close_strength"].iloc[i]), is_cs, f"cs seed={seed} bar={i}")
                self.assertEqual(bool(flags["volume_surge"].iloc[i]), is_surge, f"surge seed={seed} bar={i}")
                fired["close_strength"] += is_cs
                fired["volume_surge"] += is_surge
        self.assertTrue(all(n > 0 for n in fired.values()), fired)  # not a vacuous pass

    def test_event_signals_stay_on_for_recent_bars_then_expire(self):
        df = _with_real_ranges(1)
        short, long_ = signal_flags(df, recent=1), signal_flags(df, recent=3)
        for name in EVENT_SIGNALS:
            self.assertTrue((long_[name] | ~short[name]).all(), name)  # recent=3 is a superset of recent=1
            if short[name].sum():
                self.assertGreater(long_[name].sum(), short[name].sum(), name)
        self.assertEqual(set(signal_flags(df, 3)), set(SIGNALS))


class TestWhaleSeries(unittest.TestCase):
    def test_flag_equals_live_strategy_with_todays_flow_unconfirmed(self):
        df = _with_real_ranges(3)
        rng = np.random.default_rng(7)
        turnover = df["close"] * df["volume"]
        flow = pd.DataFrame(
            {"institutional_net": turnover * rng.normal(0.02, 0.08, len(df)), "foreign_net": turnover * rng.normal(0.02, 0.08, len(df))},
            index=df.index,
        )
        _ratio, flag = whale_series(df, flow)
        strategy, fired = WhaleFlowStrategy(window=5, buy_threshold_ratio=0.05), 0
        for i in range(10, len(df)):
            window = df.iloc[: i + 1].copy()
            window["institutional_net"], window["foreign_net"] = flow["institutional_net"], flow["foreign_net"]
            window.iloc[-1, window.columns.get_loc("institutional_net")] = np.nan  # KRX hasn't confirmed today yet
            window.iloc[-1, window.columns.get_loc("foreign_net")] = np.nan
            expected = strategy.generate_signal("X", window).signal == Signal.BUY
            self.assertEqual(bool(flag.iloc[i]), expected, f"bar={i}")
            fired += expected
        self.assertGreater(fired, 0)  # not a vacuous pass


class TestSetupEntryStrategy(unittest.TestCase):
    def test_buys_only_when_the_day_flagged_the_symbol(self):
        df = _with_real_ranges(2)
        day = df.index[100]
        candidates = pd.DataFrame(False, index=df.index, columns=["X"])
        strategy = SetupEntryStrategy(candidates)
        self.assertNotEqual(strategy.generate_signal("X", df.iloc[:101]).signal, Signal.BUY)
        candidates.at[day, "X"] = True
        self.assertEqual(strategy.generate_signal("X", df.iloc[:101]).signal, Signal.BUY)


if __name__ == "__main__":
    unittest.main()
