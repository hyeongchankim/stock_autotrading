"""Re-entry policy (2026-10-13: RSI sold 두산테스나 as overbought, close_strength bought it back 31 s later, x3).

RiskManager remembers today's exits (and persists them); the engine's reentry_policy decides what to do when a
strategy SELL and a BUY vote meet: "off" = unchanged, "block_day" = no re-buy the same day, "net_same_cycle" = skip a
sell that would be bought straight back."""
from __future__ import annotations

import unittest
from datetime import date

import numpy as np
import pandas as pd

from broker.mock_broker import MockBroker
from engine.trading_engine import TradingEngine
from risk.risk_manager import RiskManager
from strategies.base import Signal, Strategy, StrategyCategory, StrategySignal
from strategies.volume_filter import VolumeFilter

DAY1, DAY2 = date(2026, 10, 13), date(2026, 10, 14)


class _Scripted(Strategy):
    """Votes whatever the current script says for a symbol: {symbol: Signal}."""
    category = StrategyCategory.TREND_FOLLOWING

    def __init__(self, name, script):
        self.name, self.script = name, script

    def min_bars_required(self):
        return 1

    def generate_signal(self, symbol, ohlcv):
        return StrategySignal(symbol, self.script.get(symbol, Signal.HOLD), self.name, "test")


def _bars(price=100.0, volume=1000):
    idx = pd.date_range("2026-10-01", periods=25, freq="D")
    c = np.full(25, price)
    vol = np.full(25, 1000.0)
    vol[-1] = volume
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": vol}, index=idx)


def _engine(policy, volume_filter=None):
    broker = MockBroker(seed_capital=1_000_000)
    risk = RiskManager(seed_capital=1_000_000, position_size_pct=0.30)
    engine = TradingEngine(
        broker=broker, data_feed=None, risk_manager=risk, watchlist=[], notify=False,
        strategies=[_Scripted("trend_buy", {"X": Signal.BUY}), _Scripted("rsi_sell", {})],
        reentry_policy=policy, volume_filter=volume_filter,
    )
    return engine, broker, risk


def _run(engine, day, windows):
    engine.run_once(as_of=day, precomputed_windows=windows)


def _fills(broker, side):
    return [o for o in broker.order_log if o.side == side and o.filled]


class TestRiskManagerExits(unittest.TestCase):
    def test_exits_are_remembered_per_day_and_cleared_on_the_next(self):
        rm = RiskManager(seed_capital=1_000_000)
        rm.roll_to_day(DAY1)
        self.assertFalse(rm.has_exited_today("A"))
        rm.record_exit("A")
        self.assertTrue(rm.has_exited_today("A"))
        rm.roll_to_day(DAY1)            # same day again: kept
        self.assertTrue(rm.has_exited_today("A"))
        rm.roll_to_day(DAY2)            # new day: cleared
        self.assertFalse(rm.has_exited_today("A"))

    def test_survives_a_save_and_restore_and_old_state_files_without_the_key(self):
        rm = RiskManager(seed_capital=1_000_000)
        rm.roll_to_day(DAY1)
        rm.record_exit("A")
        rm.record_exit("B")
        snapshot = rm.to_dict()
        self.assertEqual(snapshot["exited_today"], ["A", "B"])
        fresh = RiskManager(seed_capital=1_000_000)
        fresh.restore(snapshot)
        fresh.roll_to_day(DAY1)         # the next process run, same day: still remembered
        self.assertTrue(fresh.has_exited_today("A") and fresh.has_exited_today("B"))
        stale = RiskManager(seed_capital=1_000_000)
        stale.restore(snapshot)
        stale.roll_to_day(DAY2)         # yesterday's snapshot on a new day: cleared
        self.assertFalse(stale.has_exited_today("A"))
        legacy = RiskManager(seed_capital=1_000_000)
        legacy.restore({"date": "2026-10-13", "daily_realized_pnl": 5.0, "trading_halted_today": False})  # pre-change file
        self.assertEqual(legacy.exited_today, set())


class TestPolicyValidation(unittest.TestCase):
    def test_unknown_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            _engine("sometimes")


class TestBlockDay(unittest.TestCase):
    def test_a_strategy_exit_is_not_followed_by_a_rebuy_that_day_but_is_allowed_the_next(self):
        engine, broker, _ = _engine("block_day")
        w = {"X": _bars()}
        _run(engine, DAY1, w)                                   # enters
        self.assertIn("X", broker.get_positions())
        engine.strategies[1].script["X"] = Signal.SELL
        with self.assertLogs("engine", level="INFO") as logs:
            _run(engine, DAY1, w)                               # RSI exit; the BUY vote must not re-enter
        self.assertNotIn("X", broker.get_positions())
        self.assertTrue(any("re-entry blocked" in line for line in logs.output))
        engine.strategies[1].script.clear()
        _run(engine, DAY1, w)                                   # RSI quiet again: still blocked today
        self.assertNotIn("X", broker.get_positions())
        _run(engine, DAY2, w)                                   # next day: free again
        self.assertIn("X", broker.get_positions())

    def test_a_stop_loss_also_blocks_the_rest_of_the_day_but_other_symbols_are_unaffected(self):
        engine, broker, _ = _engine("block_day")
        engine.strategies[0].script["Y"] = Signal.BUY
        _run(engine, DAY1, {"X": _bars(100), "Y": _bars(100)})
        self.assertEqual(set(broker.get_positions()), {"X", "Y"})
        _run(engine, DAY1, {"X": _bars(80), "Y": _bars(100)})   # X -20% -> stopped out despite the BUY vote
        self.assertEqual(set(broker.get_positions()), {"Y"})


class TestNetSameCycle(unittest.TestCase):
    def test_a_sell_that_would_be_bought_straight_back_is_skipped_so_no_round_trip_is_paid(self):
        engine, broker, _ = _engine("net_same_cycle")
        w = {"X": _bars()}
        _run(engine, DAY1, w)
        engine.strategies[1].script["X"] = Signal.SELL
        with self.assertLogs("engine", level="INFO") as logs:
            _run(engine, DAY1, w)
        self.assertIn("X", broker.get_positions())
        self.assertEqual((len(_fills(broker, Signal.SELL)), len(_fills(broker, Signal.BUY))), (0, 1))
        self.assertTrue(any("exit skipped" in line for line in logs.output))

    def test_a_sell_with_no_buy_vote_still_exits(self):
        engine, broker, _ = _engine("net_same_cycle")
        w = {"X": _bars()}
        _run(engine, DAY1, w)
        engine.strategies[0].script.clear()                    # trend vote gone
        engine.strategies[1].script["X"] = Signal.SELL
        _run(engine, DAY1, w)
        self.assertNotIn("X", broker.get_positions())

    def test_a_sell_still_exits_when_the_buy_would_be_vetoed_by_an_entry_filter(self):
        # volume today is below 1.5x the prior average, so the VolumeFilter vetoes any (re)entry
        engine, broker, _ = _engine("net_same_cycle", volume_filter=VolumeFilter(window=20, multiplier=1.5))
        engine.run_once(as_of=DAY1, precomputed_windows={"X": _bars(volume=3000)})   # enters on a volume surge
        self.assertIn("X", broker.get_positions())
        engine.strategies[1].script["X"] = Signal.SELL
        engine.run_once(as_of=DAY1, precomputed_windows={"X": _bars(volume=1000)})   # no surge: a re-buy could not happen
        self.assertNotIn("X", broker.get_positions())

    def test_a_stop_loss_is_never_skipped_and_is_not_undone_by_a_same_cycle_rebuy(self):
        engine, broker, _ = _engine("net_same_cycle")
        _run(engine, DAY1, {"X": _bars(100)})
        with self.assertLogs("engine", level="INFO") as logs:
            _run(engine, DAY1, {"X": _bars(80)})               # -20%: stop-loss, while the BUY vote is still standing
        self.assertNotIn("X", broker.get_positions())          # stopped out and NOT bought back at 80
        self.assertEqual((len(_fills(broker, Signal.SELL)), len(_fills(broker, Signal.BUY))), (1, 1))
        self.assertTrue(any("stopped out" in line for line in logs.output))
        _run(engine, DAY1, {"X": _bars(80)})                   # a later cycle may legitimately enter again
        self.assertIn("X", broker.get_positions())


class TestOffIsUnchanged(unittest.TestCase):
    def test_with_the_policy_off_the_old_sell_then_rebuy_happens(self):
        engine, broker, _ = _engine("off")
        w = {"X": _bars()}
        _run(engine, DAY1, w)
        engine.strategies[1].script["X"] = Signal.SELL
        _run(engine, DAY1, w)
        self.assertIn("X", broker.get_positions())
        self.assertEqual((len(_fills(broker, Signal.SELL)), len(_fills(broker, Signal.BUY))), (1, 2))




class TestRealConfig(unittest.TestCase):
    """The shipped config.yaml must build working engines. (YAML reads an unquoted `off` as the boolean False -
    caught only because this test loads the real file; the unit tests above never touch it.)"""

    def test_the_policy_in_config_yaml_is_a_valid_string_and_every_engine_builds(self):
        from pathlib import Path
        from unittest.mock import patch

        import yaml

        import main as app

        cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8"))
        policy = cfg["signal_combination"]["reentry_policy"]
        self.assertIsInstance(policy, str)
        self.assertIn(policy, ("off", "block_day", "net_same_cycle"))
        engine = app.build_engine(cfg, MockBroker(seed_capital=1_000_000), data_feed=None, seed_capital=1_000_000, notify=False)
        self.assertEqual(engine.reentry_policy, policy)
        # the MACD and discovery sleeves build their TradingEngine directly - make sure they accept the same value
        for fn in (app.run_macd_sleeve, app.run_discovery):
            captured = {}
            real = app.TradingEngine

            def spy(*args, **kwargs):
                captured["policy"] = kwargs.get("reentry_policy")
                raise RuntimeError("stop after constructing")

            with patch.object(app, "TradingEngine", side_effect=spy), patch.object(app, "market_closed", return_value=False), \
                 patch.object(app, "build_broker", return_value=MockBroker(seed_capital=1_000_000)), \
                 patch.object(app, "build_data_feed", return_value=None), patch.object(app, "fill_outcomes"), \
                 patch.object(app, "get_universe", return_value={}), patch.object(app, "KisSession"), patch.object(app, "KisDataFeed"), \
                 patch.object(app, "universe_candidates", side_effect=RuntimeError("stop before trading")):
                try:
                    fn(cfg) if fn is app.run_macd_sleeve else fn(cfg, "scan")
                except RuntimeError:
                    pass
            if fn is app.run_macd_sleeve:
                self.assertEqual(captured.get("policy"), policy)

if __name__ == "__main__":
    unittest.main()
