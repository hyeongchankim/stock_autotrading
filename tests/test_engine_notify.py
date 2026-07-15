"""Sanity checks for TradingEngine's notification hooks (entry/exit fills
and failures) - not full engine behavior, that's covered elsewhere.

Run with: pytest
(or: python -m unittest tests.test_engine_notify)
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from broker.base import BrokerBase, OrderResult
from broker.mock_broker import MockBroker
from engine.trading_engine import TradingEngine
from portfolio.portfolio import Position
from risk.risk_manager import RiskManager
from strategies.base import Signal, Strategy, StrategyCategory, StrategySignal


def _make_ohlcv(n: int = 5, close: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    closes = np.full(n, close)
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1000},
        index=dates,
    )


class _AlwaysBuyStrategy(Strategy):
    name = "always_buy"
    category = StrategyCategory.TREND_FOLLOWING

    def min_bars_required(self) -> int:
        return 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        return StrategySignal(symbol=symbol, signal=Signal.BUY, strategy_name=self.name, reason="test")


class _AlwaysSellStrategy(Strategy):
    name = "always_sell"
    category = StrategyCategory.TREND_FOLLOWING

    def min_bars_required(self) -> int:
        return 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        return StrategySignal(symbol=symbol, signal=Signal.SELL, strategy_name=self.name, reason="test")


class _AlwaysHoldStrategy(Strategy):
    name = "always_hold"
    category = StrategyCategory.TREND_FOLLOWING

    def min_bars_required(self) -> int:
        return 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        return self._hold(symbol, "test")


class _RejectingBroker(BrokerBase):
    """Always reports a filled=False SELL, to exercise the failed-exit
    notification path without needing a real broker error scenario.
    """

    def __init__(self):
        self._positions = {"A": Position(symbol="A", quantity=5, avg_price=100.0)}

    def get_cash_balance(self) -> float:
        return 0.0

    def get_positions(self) -> dict:
        return self._positions

    def get_current_price(self, symbol: str) -> float:
        return 80.0  # below avg_price, so a stop-loss trigger fires

    def place_order(self, symbol, side, quantity, price=None) -> OrderResult:
        return OrderResult(symbol, side, quantity, price or 0.0, False, "insufficient position")


class TestEngineEntryExitNotifications(unittest.TestCase):
    @patch("engine.trading_engine.send_notification")
    def test_entry_fill_sends_notification(self, mock_notify):
        broker = MockBroker(seed_capital=100_000)
        risk_manager = RiskManager(seed_capital=100_000, position_size_pct=0.5)
        engine = TradingEngine(
            broker=broker, data_feed=None, strategies=[_AlwaysBuyStrategy()],
            risk_manager=risk_manager, watchlist=["A"],
        )
        engine.run_once(precomputed_windows={"A": _make_ohlcv()})

        self.assertTrue(mock_notify.called)
        message = mock_notify.call_args.args[0]
        self.assertIn("entry", message)
        self.assertIn("A", message)
        self.assertIn("[mock]", message)

    @patch("engine.trading_engine.send_notification")
    def test_strategy_exit_fill_sends_notification(self, mock_notify):
        broker = MockBroker(seed_capital=100_000)
        broker.set_price_lookup({"A": 100.0})
        broker.place_order("A", Signal.BUY, 10, 100.0)  # pre-existing position
        risk_manager = RiskManager(seed_capital=100_000)
        engine = TradingEngine(
            broker=broker, data_feed=None, strategies=[_AlwaysSellStrategy()],
            risk_manager=risk_manager, watchlist=["A"],
        )
        engine.run_once(precomputed_windows={"A": _make_ohlcv(close=100.0)})

        messages = [c.args[0] for c in mock_notify.call_args_list]
        self.assertTrue(any("strategy exit" in m and "A" in m for m in messages))

    @patch("engine.trading_engine.send_notification")
    def test_protective_exit_fill_sends_notification(self, mock_notify):
        broker = MockBroker(seed_capital=100_000)
        broker.set_price_lookup({"A": 100.0})
        broker.place_order("A", Signal.BUY, 10, 100.0)
        risk_manager = RiskManager(seed_capital=100_000, stop_loss_pct=0.05)
        engine = TradingEngine(
            broker=broker, data_feed=None, strategies=[_AlwaysHoldStrategy()],
            risk_manager=risk_manager, watchlist=["A"],
        )
        # price drops 20% - well past the 5% stop-loss
        engine.run_once(precomputed_windows={"A": _make_ohlcv(close=80.0)})

        messages = [c.args[0] for c in mock_notify.call_args_list]
        self.assertTrue(any("STOP_LOSS" in m and "A" in m for m in messages))

    @patch("engine.trading_engine.send_notification")
    def test_failed_exit_sends_failure_notification(self, mock_notify):
        broker = _RejectingBroker()
        risk_manager = RiskManager(seed_capital=100_000, stop_loss_pct=0.05)
        engine = TradingEngine(
            broker=broker, data_feed=None, strategies=[_AlwaysHoldStrategy()],
            risk_manager=risk_manager, watchlist=["A"],
        )
        engine.run_once(precomputed_windows={"A": _make_ohlcv(close=80.0)})

        messages = [c.args[0] for c in mock_notify.call_args_list]
        self.assertTrue(any("FAILED" in m and "A" in m for m in messages))


if __name__ == "__main__":
    unittest.main()
