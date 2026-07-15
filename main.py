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

from broker.base import BrokerBase
from broker.kis_broker import KisBroker
from broker.mock_broker import MockBroker
from data.base import DataFeedBase
from data.kis_data_feed import KisDataFeed
from data.krx_investor_feed import KrxInvestorFlowFeed, WhaleEnrichedDataFeed
from data.yfinance_feed import YFinanceDataFeed
from engine.trading_engine import TradingEngine
from portfolio.buy_and_hold import BuyAndHoldSleeve
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
from strategies.whale_flow import WhaleFlowStrategy
from utils.logger import setup_logging
from utils.state_store import StateStore

setup_logging()
logger = logging.getLogger("main")

STATE_FILE = Path(__file__).parent / "state.json"


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
    if tf.get("whale_flow", {}).get("enabled"):
        strategies.append(
            WhaleFlowStrategy(
                window=tf["whale_flow"]["window"],
                buy_threshold_ratio=tf["whale_flow"]["buy_threshold_ratio"],
                sell_threshold_ratio=tf["whale_flow"]["sell_threshold_ratio"],
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


def build_broker(config: dict, seed_capital: float | None = None) -> BrokerBase:
    """Returns MockBroker unless broker.provider is "kis", in which case it
    connects to the real KIS account (see broker.kis.env - defaults to
    demo/paper trading, never assume "real"). seed_capital scopes a local
    cash ledger for KisBroker too (see KisBroker docstring) - the real
    account's actual balance isn't tied to this bot's configured seed, so
    without it position sizing would be based on whatever happens to be in
    the account instead of the intended budget.
    """
    broker_cfg = config.get("broker", {})
    if broker_cfg.get("provider") == "kis":
        kis_cfg = broker_cfg.get("kis", {})
        return KisBroker(
            env=kis_cfg.get("env", "demo"),
            watchlist=config["watchlist"],
            seed_capital=seed_capital,
        )

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


def build_data_feed(config: dict) -> DataFeedBase:
    """yfinance by default, or the real KIS quote API when broker.provider
    is "kis" (same env - demo/real - as the broker). When whale_flow is
    enabled, wraps whichever base feed in WhaleEnrichedDataFeed so every
    fetch also carries institutional_net/foreign_net columns (KRX
    Information Data System - requires KRX_ID/KRX_PW env vars, degrades
    gracefully to plain OHLCV if that login isn't available).
    """
    broker_cfg = config.get("broker", {})
    if broker_cfg.get("provider") == "kis":
        kis_cfg = broker_cfg.get("kis", {})
        base_feed: DataFeedBase = KisDataFeed(env=kis_cfg.get("env", "demo"))
    else:
        base_feed = YFinanceDataFeed()

    whale_cfg = config["strategies"]["trend_following"].get("whale_flow", {})
    if not whale_cfg.get("enabled", False):
        return base_feed
    return WhaleEnrichedDataFeed(base_feed, KrxInvestorFlowFeed())


def build_engine(
    config: dict, broker: BrokerBase, data_feed: DataFeedBase, seed_capital: float | None = None
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
    total_seed = config["seed_capital"]
    alloc_pct = strategy_allocation_pct(config)
    strategy_seed = total_seed * alloc_pct
    bh_seed = total_seed - strategy_seed

    broker = build_broker(config, seed_capital=strategy_seed)
    data_feed = build_data_feed(config)
    engine = build_engine(config, broker, data_feed, seed_capital=strategy_seed)

    store = StateStore(STATE_FILE)
    state = store.load()
    engine.risk_manager.restore(state.get("risk_manager", {}))
    if isinstance(broker, KisBroker):
        broker.restore_ledger(state.get("kis_cash_ledger", {}))

    # Fetched once here (rather than left to engine.run_once) so the same
    # day_prices can also mark-to-market the buy_and_hold sleeve below,
    # without a second round of API calls per symbol.
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
        "risk_manager": engine.risk_manager.to_dict(),
        "buy_and_hold": bh_sleeve.to_dict() if bh_sleeve else {},
        "kis_cash_ledger": broker.ledger_to_dict() if isinstance(broker, KisBroker) else {},
    })

    if bh_sleeve is not None:
        bh_value = bh_sleeve.current_value(day_prices)
        logger.info(
            "cycle complete. strategy: cash=%.0f positions=%s equity=%.0f | "
            "buy_and_hold: equity=%.0f | combined=%.0f",
            broker.get_cash_balance(), list(broker.get_positions().keys()), strategy_equity,
            bh_value, strategy_equity + bh_value,
        )
    else:
        logger.info(
            "cycle complete. cash=%.0f positions=%s total_equity=%.0f",
            broker.get_cash_balance(), list(broker.get_positions().keys()), strategy_equity,
        )


def run_backtest(config: dict) -> None:
    from backtest.backtest_runner import BacktestRunner
    from backtest.benchmark import run_buy_and_hold
    from backtest.metrics import max_drawdown_pct, trade_stats

    total_seed = config["seed_capital"]
    alloc_pct = strategy_allocation_pct(config)
    strategy_seed = total_seed * alloc_pct
    bh_seed = total_seed - strategy_seed

    # Backtesting always uses MockBroker + yfinance regardless of
    # broker.provider - a real broker's API isn't meant for bulk historical
    # queries (rate limits, and it reports live account state, not a
    # simulated seed). whale_flow enrichment still applies if enabled.
    costs_cfg = config.get("costs", {})
    broker = MockBroker(
        seed_capital=strategy_seed,
        commission_pct=costs_cfg.get("commission_pct", 0.0),
        sell_tax_pct=costs_cfg.get("sell_tax_pct", 0.0),
    )
    base_feed = YFinanceDataFeed()
    whale_cfg = config["strategies"]["trend_following"].get("whale_flow", {})
    data_feed: DataFeedBase = (
        WhaleEnrichedDataFeed(base_feed, KrxInvestorFlowFeed()) if whale_cfg.get("enabled", False) else base_feed
    )
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
