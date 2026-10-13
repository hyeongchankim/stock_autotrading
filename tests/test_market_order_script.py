"""scripts/market_order_test.py: stock selection is the only logic worth testing offline; the rest is a manual
demo-account check that needs market hours."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import pandas as pd

_spec = importlib.util.spec_from_file_location(
    "market_order_test", Path(__file__).resolve().parent.parent / "scripts" / "market_order_test.py")
mot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mot)


def _q(rows):
    return pd.DataFrame(rows, columns=["ticker", "name", "price", "trading_value"])


class TestPickTestSymbol(unittest.TestCase):
    QUOTES = _q([
        ("000001", "cheap-but-illiquid", 6_000, 2e9),     # below the liquidity floor
        ("000002", "cheap-and-held", 7_000, 5e10),        # a sleeve holds it
        ("000003", "cheap-and-busy", 8_000, 5e10),        # in today's top candidates
        ("000004", "ok-cheaper", 9_000, 5e10),
        ("000005", "ok-dearer", 20_000, 5e10),
        ("000006", "too-dear", 90_000, 5e10),
        ("000007", "too-cheap", 3_000, 5e10),
    ])

    def test_picks_the_cheapest_liquid_stock_that_is_neither_held_nor_busy(self):
        self.assertEqual(mot.pick_test_symbol(self.QUOTES, held_tickers={"000002"}, busy_tickers={"000003"}), "000004")

    def test_returns_none_when_nothing_qualifies(self):
        self.assertIsNone(mot.pick_test_symbol(self.QUOTES, held_tickers={"000004", "000005"}, busy_tickers={"000002", "000003"}))


class TestSafetyGates(unittest.TestCase):
    def test_the_script_refuses_when_the_configured_env_is_not_demo(self):
        from unittest.mock import patch

        real_cfg = "broker:\n  kis:\n    env: real\ndiscovery: {}\n"
        with patch.object(Path, "read_text", return_value=real_cfg), patch.object(mot.sys, "argv", ["x", "--place"]):
            self.assertEqual(mot.main(), 2)   # before any broker / network object is created


if __name__ == "__main__":
    unittest.main()
