"""screening/dynamic_watchlist.py + main.dynamic_symbols: affordable strongest
movers first, held positions always kept, and every failure mode degrades to
'held positions only' (exits still run) instead of crashing the cycle."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import main as app
from screening import dynamic_watchlist as dw

DISC = {"min_price": 5000, "max_change_pct": 15.0, "min_trading_value": 3_000_000_000}


def _candidates() -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": ["A.KS", "B.KQ", "C.KS", "D.KS", "E.KQ"],
        "price": [100_000.0, 50_000.0, 1_800_000.0, 140_000.0, 20_000.0],
        "change_pct": [3.0, 9.0, 12.0, 6.0, 1.0],
    })


class TestPickAndMerge(unittest.TestCase):
    def test_drops_unaffordable_and_orders_by_change(self):
        # budget 150k/share: C (1.8M) can't be bought even though it moved most
        self.assertEqual(dw.pick_symbols(_candidates(), 10, 150_000), ["B.KQ", "D.KS", "A.KS", "E.KQ"])

    def test_caps_the_count(self):
        self.assertEqual(dw.pick_symbols(_candidates(), 2, 150_000), ["B.KQ", "D.KS"])

    def test_merge_keeps_mover_order_and_appends_held_once(self):
        self.assertEqual(dw.merge_with_held(["B", "D"], ["X", "D", "Y"]), ["B", "D", "X", "Y"])
        self.assertEqual(dw.merge_with_held([], ["X"]), ["X"])

    def test_current_movers_applies_the_discovery_filters_then_affordability(self):
        scan = pd.DataFrame({
            "ticker": ["000001", "000002", "000003", "000004"], "name": list("abcd"),
            "price": [50_000.0, 60_000.0, 40_000.0, 900_000.0], "change_pct": [8.0, 5.0, 9.0, 11.0],
            "volume": [100_000, 100_000, 10, 100_000],  # 000003: 40k * 10 = far below 30억
        })
        universe = {"000001.KS": "a", "000002.KQ": "b", "000003.KS": "c", "000004.KS": "d"}  # 000009 etc. not in it
        with patch.object(dw, "scan_market", return_value=scan):
            movers = dw.current_movers(object(), universe, DISC, max_candidates=10, max_price=150_000)
        # 000003 illiquid, 000004 unaffordable -> the two left, strongest first
        self.assertEqual(movers, ["000001.KS", "000002.KQ"])


class _Broker:
    def __init__(self, held, session=True):
        self._held = held
        if session:
            self.session = object()

    def get_positions(self):
        return {s: None for s in self._held}


CFG = {
    "discovery": {**DISC, "universe_cache_days": 7},
    "dynamic_watchlist": {"enabled": True, "max_candidates": 5, "max_price": None},
    "risk": {"position_size_pct": 0.30},
}


class TestDynamicSymbols(unittest.TestCase):
    def test_happy_path_merges_movers_with_held_and_prices_by_position_budget(self):
        with patch.object(app, "get_universe", return_value={"X": "x"}), \
             patch.object(app, "current_movers", return_value=["B.KQ", "D.KS"]) as movers:
            result = app.dynamic_symbols(CFG, _Broker(["H.KS", "D.KS"]), strategy_seed=500_000.0)
        self.assertEqual(result, ["B.KQ", "D.KS", "H.KS"])
        self.assertEqual(movers.call_args.args[4], 150_000.0)  # max_price = 500k x 30%

    def test_explicit_max_price_overrides_the_budget_rule(self):
        cfg = {**CFG, "dynamic_watchlist": {"enabled": True, "max_candidates": 5, "max_price": 90_000}}
        with patch.object(app, "get_universe", return_value={}), \
             patch.object(app, "current_movers", return_value=[]) as movers:
            app.dynamic_symbols(cfg, _Broker([]), strategy_seed=500_000.0)
        self.assertEqual(movers.call_args.args[4], 90_000)

    def test_scan_failure_degrades_to_held_only(self):
        with patch.object(app, "get_universe", return_value={}), \
             patch.object(app, "current_movers", side_effect=RuntimeError("KIS down")):
            self.assertEqual(app.dynamic_symbols(CFG, _Broker(["H.KS"]), 500_000.0), ["H.KS"])

    def test_universe_failure_degrades_to_held_only(self):
        with patch.object(app, "get_universe", side_effect=RuntimeError("KRX blocked, no cache")):
            self.assertEqual(app.dynamic_symbols(CFG, _Broker(["H.KS"]), 500_000.0), ["H.KS"])

    def test_broker_without_a_kis_session_degrades_to_held_only(self):
        self.assertEqual(app.dynamic_symbols(CFG, _Broker(["H.KS"], session=False), 500_000.0), ["H.KS"])


if __name__ == "__main__":
    unittest.main()
