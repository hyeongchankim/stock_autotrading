#!/usr/bin/env python3
"""Entry point for the stock auto-trading skeleton.

Usage:
    python main.py --mode paper       # one paper-trading cycle against near-live data
    python main.py --mode backtest    # replay strategies over historical data
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import yaml

from broker.mock_broker import MockBroker
from data.yfinance_feed import YFinanceDataFeed
from engine.trading_engine import TradingEngine
from risk.risk_manager import RiskManager
from strategies.mean_reversion import RSIStrategy, VolatilityBreakoutStrategy
from strategies.regime import RegimeFilter
from strategies.trend_following import (
    BollingerBreakoutStrategy,
    DonchianBreakoutStrategy,
    MACDStrategy,
    MovingAverageCrossStrategy,
)
from strategies.volume_filter import VolumeFilter
from utils.logger import setup_logging

setup_logging()
logger = logging.getLogger("main")


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_strategies(config: dict) -> list:
    strategies = []
    tf = config["strategies"]["trend_following"]
    mr = config["strategies"]["mean_reversion"]

    if tf["ma_cross"]["enabled"]:
        strategies.append(
            MovingAverageCrossStrategy(
                short_window=tf["ma_cross"]["short_window"],
                long_window=tf["ma_cross"]["long_window"],
            )
        )
    if tf["bollinger_breakout"]["enabled"]:
        strategies.append(
            BollingerBreakoutStrategy(
                window=tf["bollinger_breakout"]["window"],
                num_std=tf["bollinger_breakout"]["num_std"],
            )
        )
    if tf.get("donchian_breakout", {}).get("enabled"):
        strategies.append(
            DonchianBreakoutStrategy(
                entry_window=tf["donchian_breakout"]["entry_window"],
                exit_window=tf["donchian_breakout"]["exit_window"],
            )
        )
    if tf.get("macd", {}).get("enabled"):
        strategies.append(
            MACDStrategy(
                fast=tf["macd"]["fast"],
                slow=tf["macd"]["slow"],
                signal=tf["macd"]["signal"],
            )
        )
    if mr["rsi"]["enabled"]:
        strategies.append(
            RSIStrategy(
                period=mr["rsi"]["period"],
                oversold=mr["rsi"]["oversold"],
                overbought=mr["rsi"]["overbought"],
            )
        )
    if mr["volatility_breakout"]["enabled"]:
        strategies.append(VolatilityBreakoutStrategy(k=mr["volatility_breakout"]["k"]))

    return strategies


def build_risk_manager(config: dict) -> RiskManager:
    risk_cfg = config["risk"]
    return RiskManager(
        seed_capital=config["seed_capital"],
        stop_loss_pct=risk_cfg["stop_loss_pct"],
        take_profit_pct=risk_cfg["take_profit_pct"],
        position_size_pct=risk_cfg["position_size_pct"],
        daily_max_loss_pct=risk_cfg["daily_max_loss_pct"],
    )


def build_regime_filter(config: dict) -> RegimeFilter | None:
    rf_cfg = config.get("regime_filter", {})
    if not rf_cfg.get("enabled", False):
        return None
    return RegimeFilter(
        adx_period=rf_cfg["adx_period"],
        trend_threshold=rf_cfg["trend_threshold"],
        range_threshold=rf_cfg["range_threshold"],
    )


def build_broker(config: dict) -> MockBroker:
    costs_cfg = config.get("costs", {})
    return MockBroker(
        seed_capital=config["seed_capital"],
        commission_pct=costs_cfg.get("commission_pct", 0.0),
        sell_tax_pct=costs_cfg.get("sell_tax_pct", 0.0),
    )


def build_volume_filter(config: dict) -> VolumeFilter | None:
    vf_cfg = config.get("volume_filter", {})
    if not vf_cfg.get("enabled", False):
        return None
    return VolumeFilter(window=vf_cfg["window"], multiplier=vf_cfg["multiplier"])


def build_engine(config: dict, broker: MockBroker, data_feed: YFinanceDataFeed) -> TradingEngine:
    signal_cfg = config.get("signal_combination", {})
    return TradingEngine(
        broker=broker,
        data_feed=data_feed,
        strategies=build_strategies(config),
        risk_manager=build_risk_manager(config),
        watchlist=config["watchlist"],
        interval=config["data_feed"]["interval"],
        lookback=config["data_feed"]["lookback_days"],
        regime_filter=build_regime_filter(config),
        entry_mode=signal_cfg.get("entry_mode", "any"),
        volume_filter=build_volume_filter(config),
        rank_entries_by_momentum=signal_cfg.get("rank_entries_by_momentum", False),
        momentum_window=signal_cfg.get("momentum_window", 20),
    )


def run_paper(config: dict) -> None:
    broker = build_broker(config)
    data_feed = YFinanceDataFeed()
    engine = build_engine(config, broker, data_feed)

    equity = engine.run_once()
    logger.info(
        "cycle complete. cash=%.0f positions=%s total_equity=%.0f",
        broker.get_cash_balance(), list(broker.get_positions().keys()), equity,
    )


def run_backtest(config: dict) -> None:
    from backtest.backtest_runner import BacktestRunner
    from backtest.metrics import max_drawdown_pct, trade_stats

    broker = build_broker(config)
    data_feed = YFinanceDataFeed()
    engine = build_engine(config, broker, data_feed)

    history_days = config.get("backtest", {}).get("history_days", 500)
    symbol_data = {}
    for symbol in config["watchlist"]:
        try:
            symbol_data[symbol] = data_feed.get_ohlcv(symbol, config["data_feed"]["interval"], lookback=history_days)
        except Exception as exc:
            logger.warning("skipping %s: failed to fetch data (%s)", symbol, exc)

    strategy_min_bars = max(s.min_bars_required() for s in engine.strategies)
    regime_cfg = config.get("regime_filter", {})
    regime_min_bars = regime_cfg["adx_period"] * 2 + 5 if regime_cfg.get("enabled") else 0
    min_bars = max(strategy_min_bars, regime_min_bars)

    runner = BacktestRunner(engine, min_bars=min_bars)
    equity_curve = runner.run(symbol_data)

    start_equity = config["seed_capital"]
    end_equity = float(equity_curve["equity"].iloc[-1])
    total_return_pct = (end_equity / start_equity - 1) * 100
    mdd_pct = max_drawdown_pct(equity_curve["equity"])
    stats = trade_stats(broker.order_log)

    logger.info(
        "backtest complete. start=%.0f end=%.0f return=%.2f%% max_drawdown=%.2f%% "
        "round_trips=%d win_rate=%.1f%% orders=%d",
        start_equity, end_equity, total_return_pct, mdd_pct,
        stats["num_round_trips"], stats["win_rate_pct"], len(broker.order_log),
    )
    print(equity_curve.tail(10))


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock auto-trading skeleton")
    parser.add_argument("--mode", choices=["paper", "backtest"], default="paper")
    parser.add_argument("--config", default=str(Path(__file__).parent / "config.yaml"))
    args = parser.parse_args()

    config = load_config(args.config)

    if args.mode == "paper":
        run_paper(config)
    else:
        run_backtest(config)


if __name__ == "__main__":
    main()
