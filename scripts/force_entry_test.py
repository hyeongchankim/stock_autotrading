#!/usr/bin/env python3
"""One-off manual test: forces a BUY signal through the full trading pipeline
(risk manager -> broker.place_order) on the configured KIS account, to verify
end-to-end order execution without waiting for a natural strategy signal.

Bypasses regime_filter and volume_filter so the forced signal can't be sat
out; the risk manager and broker are otherwise identical to run_paper's
normal path, so a filled order is saved to state.json and gets picked up
and managed (stop-loss/take-profit) by the regular scheduled cycles
afterward - this is not a throwaway simulation, it places a real order on
whatever account config.yaml's broker.kis.env points at (defaults to demo).

Usage:
    python scripts/force_entry_test.py [SYMBOL]

SYMBOL defaults to the first watchlist entry in config.yaml.
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from broker.kis_broker import KisBroker
from engine.trading_engine import TradingEngine
from main import (
    STATE_FILE,
    build_broker,
    build_data_feed,
    build_risk_manager,
    load_config,
    strategy_allocation_pct,
)
from portfolio.buy_and_hold import BuyAndHoldSleeve
from strategies.base import Signal, Strategy, StrategyCategory, StrategySignal
from utils.logger import setup_logging
from utils.state_store import StateStore

setup_logging()
logger = logging.getLogger("force_entry_test")


class ForceBuyStrategy(Strategy):
    """Always signals BUY for one target symbol, HOLD for everything else -
    only meant to drive a real order through the engine on demand, not a
    real trading strategy.
    """

    name = "force_buy_test"
    category = StrategyCategory.TREND_FOLLOWING

    def __init__(self, target_symbol: str):
        self.target_symbol = target_symbol

    def min_bars_required(self) -> int:
        return 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        if symbol == self.target_symbol:
            return StrategySignal(
                symbol=symbol, signal=Signal.BUY, strategy_name=self.name, reason="forced test signal"
            )
        return self._hold(symbol, "not the forced test symbol")


def main() -> None:
    config = load_config(str(Path(__file__).resolve().parent.parent / "config.yaml"))
    target_symbol = sys.argv[1] if len(sys.argv) > 1 else config["watchlist"][0]
    if target_symbol not in config["watchlist"]:
        raise SystemExit(f"{target_symbol} is not in config.yaml's watchlist")

    env = config.get("broker", {}).get("kis", {}).get("env", "demo")
    logger.info("forcing a BUY on %s against broker.kis.env=%s ...", target_symbol, env)

    total_seed = config["seed_capital"]
    alloc_pct = strategy_allocation_pct(config)
    strategy_seed = total_seed * alloc_pct
    bh_seed = total_seed - strategy_seed

    broker = build_broker(config, seed_capital=strategy_seed)
    data_feed = build_data_feed(config)
    risk_manager = build_risk_manager(config, seed_capital=strategy_seed)

    store = StateStore(STATE_FILE)
    state = store.load()
    risk_manager.restore(state.get("risk_manager", {}))
    if isinstance(broker, KisBroker):
        broker.restore_ledger(state.get("kis_cash_ledger", {}))
    risk_manager.roll_to_day(date.today())

    if not risk_manager.can_open_new_position():
        logger.error(
            "risk_manager refuses new positions right now (daily loss halt) - aborting forced test"
        )
        return

    engine = TradingEngine(
        broker=broker,
        data_feed=data_feed,
        strategies=[ForceBuyStrategy(target_symbol)],
        risk_manager=risk_manager,
        watchlist=config["watchlist"],
        interval=config["data_feed"]["interval"],
        lookback=config["data_feed"]["lookback_days"],
        regime_filter=None,  # bypassed so the forced signal can't be sat out by an ambiguous regime
        entry_mode="any",
        volume_filter=None,  # bypassed for the same reason
    )

    windows = {}
    for symbol in config["watchlist"]:
        try:
            windows[symbol] = data_feed.get_ohlcv(
                symbol, config["data_feed"]["interval"], config["data_feed"]["lookback_days"]
            )
        except Exception as exc:
            logger.warning("skipping %s: failed to fetch data (%s)", symbol, exc)
    day_prices = {symbol: float(df["close"].iloc[-1]) for symbol, df in windows.items()}

    bh_sleeve = None
    if bh_seed > 0:
        bh_sleeve = BuyAndHoldSleeve(seed_capital=bh_seed, watchlist=config["watchlist"])
        bh_sleeve.restore(state.get("buy_and_hold", {}))
        bh_sleeve.ensure_purchased(day_prices)

    strategy_equity = engine.run_once(precomputed_windows=windows)

    store.save({
        "risk_manager": risk_manager.to_dict(),
        "buy_and_hold": bh_sleeve.to_dict() if bh_sleeve else {},
        "kis_cash_ledger": broker.ledger_to_dict() if isinstance(broker, KisBroker) else {},
    })

    position = broker.get_positions().get(target_symbol)
    if position is not None:
        logger.info(
            "SUCCESS: %s position opened qty=%s avg_price=%.2f - state.json updated, "
            "the next scheduled paper cycle will manage stop-loss/take-profit on it normally",
            target_symbol, position.quantity, position.avg_price,
        )
    else:
        logger.warning(
            "no position was opened for %s - check the WARNING lines above for why "
            "(order rejected, insufficient cash, etc.)", target_symbol,
        )

    logger.info(
        "cycle complete. strategy: cash=%.0f positions=%s equity=%.0f",
        broker.get_cash_balance(), list(broker.get_positions().keys()), strategy_equity,
    )


if __name__ == "__main__":
    main()
