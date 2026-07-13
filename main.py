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


def build_risk_manager(config: dict, seed_capital: float | None = None) -> RiskManager:
    risk_cfg = config["risk"]
    return RiskManager(
        seed_capital=seed_capital if seed_capital is not None else config["seed_capital"],
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


def build_broker(config: dict, seed_capital: float | None = None) -> MockBroker:
    costs_cfg = config.get("costs", {})
    return MockBroker(
        seed_capital=seed_capital if seed_capital is not None else config["seed_capital"],
        commission_pct=costs_cfg.get("commission_pct", 0.0),
        sell_tax_pct=costs_cfg.get("sell_tax_pct", 0.0),
    )


def build_volume_filter(config: dict) -> VolumeFilter | None:
    vf_cfg = config.get("volume_filter", {})
    if not vf_cfg.get("enabled", False):
        return None
    return VolumeFilter(window=vf_cfg["window"], multiplier=vf_cfg["multiplier"])


def build_engine(
    config: dict, broker: MockBroker, data_feed: YFinanceDataFeed, seed_capital: float | None = None
) -> TradingEngine:
    signal_cfg = config.get("signal_combination", {})
    return TradingEngine(
        broker=broker,
        data_feed=data_feed,
        strategies=build_strategies(config),
        risk_manager=build_risk_manager(config, seed_capital=seed_capital),
        watchlist=config["watchlist"],
        interval=config["data_feed"]["interval"],
        lookback=config["data_feed"]["lookback_days"],
        regime_filter=build_regime_filter(config),
        entry_mode=signal_cfg.get("entry_mode", "any"),
        volume_filter=build_volume_filter(config),
        rank_entries_by_momentum=signal_cfg.get("rank_entries_by_momentum", False),
        momentum_window=signal_cfg.get("momentum_window", 20),
    )


def strategy_allocation_pct(config: dict) -> float:
    """Fraction of seed_capital run through the active strategy. The rest is
    the Buy & Hold sleeve in hybrid mode. Returns 1.0 (all-strategy) when
    hybrid mode is off.
    """
    hybrid_cfg = config.get("hybrid", {})
    if not hybrid_cfg.get("enabled", False):
        return 1.0
    return hybrid_cfg.get("strategy_allocation_pct", 1.0)


def run_paper(config: dict) -> None:
    alloc_pct = strategy_allocation_pct(config)
    strategy_seed = config["seed_capital"] * alloc_pct

    if alloc_pct < 1.0:
        logger.warning(
            "hybrid mode: paper/live tracking of the buy_and_hold sleeve is not implemented yet "
            "(needs persistent state across runs) - only the %.0f%% strategy sleeve (seed=%.0f) runs here.",
            alloc_pct * 100, strategy_seed,
        )

    broker = build_broker(config, seed_capital=strategy_seed)
    data_feed = YFinanceDataFeed()
    engine = build_engine(config, broker, data_feed, seed_capital=strategy_seed)

    equity = engine.run_once()
    logger.info(
        "cycle complete. cash=%.0f positions=%s total_equity=%.0f",
        broker.get_cash_balance(), list(broker.get_positions().keys()), equity,
    )


def run_backtest(config: dict) -> None:
    from backtest.backtest_runner import BacktestRunner
    from backtest.benchmark import run_buy_and_hold
    from backtest.metrics import max_drawdown_pct, trade_stats

    total_seed = config["seed_capital"]
    alloc_pct = strategy_allocation_pct(config)
    strategy_seed = total_seed * alloc_pct
    bh_seed = total_seed - strategy_seed

    broker = build_broker(config, seed_capital=strategy_seed)
    data_feed = YFinanceDataFeed()
    engine = build_engine(config, broker, data_feed, seed_capital=strategy_seed)

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
    strategy_curve = runner.run(symbol_data)

    strategy_end_equity = float(strategy_curve["equity"].iloc[-1])
    strategy_return_pct = (strategy_end_equity / strategy_seed - 1) * 100 if strategy_seed > 0 else 0.0
    strategy_mdd_pct = max_drawdown_pct(strategy_curve["equity"])
    stats = trade_stats(broker.order_log)

    logger.info(
        "strategy sleeve (%.0f%% of seed=%.0f). end=%.0f return=%.2f%% max_drawdown=%.2f%% "
        "round_trips=%d win_rate=%.1f%% orders=%d",
        alloc_pct * 100, strategy_seed, strategy_end_equity, strategy_return_pct, strategy_mdd_pct,
        stats["num_round_trips"], stats["win_rate_pct"], len(broker.order_log),
    )

    costs_cfg = config.get("costs", {})
    bh_curve = run_buy_and_hold(
        symbol_data,
        seed_capital=bh_seed if bh_seed > 0 else total_seed,
        start_bar_index=min_bars,
        commission_pct=costs_cfg.get("commission_pct", 0.0),
    )
    bh_end_equity = float(bh_curve["equity"].iloc[-1])
    bh_seed_for_return = bh_seed if bh_seed > 0 else total_seed
    bh_return_pct = (bh_end_equity / bh_seed_for_return - 1) * 100
    bh_mdd_pct = max_drawdown_pct(bh_curve["equity"])

    if bh_seed <= 0:
        # pure strategy mode (hybrid off): buy_and_hold is a benchmark only,
        # not capital actually allocated - report the comparison and stop.
        logger.info(
            "buy_and_hold benchmark. seed=%.0f end=%.0f return=%.2f%% max_drawdown=%.2f%% "
            "(strategy vs benchmark return: %+.2f%%p)",
            bh_seed_for_return, bh_end_equity, bh_return_pct, bh_mdd_pct,
            strategy_return_pct - bh_return_pct,
        )
        print(strategy_curve.tail(10))
        return

    combined_equity = (strategy_curve["equity"] + bh_curve["equity"]).dropna()
    combined_end_equity = float(combined_equity.iloc[-1])
    combined_return_pct = (combined_end_equity / total_seed - 1) * 100
    combined_mdd_pct = max_drawdown_pct(combined_equity)

    logger.info(
        "buy_and_hold sleeve (%.0f%% of seed=%.0f). end=%.0f return=%.2f%% max_drawdown=%.2f%%",
        (1 - alloc_pct) * 100, bh_seed, bh_end_equity, bh_return_pct, bh_mdd_pct,
    )
    logger.info(
        "hybrid combined (strategy %.0f%% / buy_and_hold %.0f%%). seed=%.0f end=%.0f "
        "return=%.2f%% max_drawdown=%.2f%%",
        alloc_pct * 100, (1 - alloc_pct) * 100, total_seed, combined_end_equity,
        combined_return_pct, combined_mdd_pct,
    )
    print(combined_equity.tail(10))


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
