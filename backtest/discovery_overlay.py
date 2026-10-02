"""Backtest: does layering a Finviz/TradingView-style pattern filter on top of
the 발굴형 종가매매 candidate list (daily 등락률 ranking) help?

Run: python -m backtest.discovery_overlay [--refresh] [--pools top5_then_pattern,pattern_then_top5]
(needs KRX_ID/KRX_PW for the universe and SSL_CERT_FILE/CURL_CA_BUNDLE on this PC)

What it reproduces vs the live sleeve (screening/discover.py + main.run_discovery):
- candidates: KOSPI200+KOSDAQ150, price >= min_price, |change%| <= max_change_pct,
  close*volume >= min_trading_value, top max_candidates by change% - from
  DAILY bars, since the KIS ranking API can't be queried historically.
- trading: the same TradingEngine/MockBroker with close_strength + regime +
  volume filters, same stop/take-profit and 20% sizing; buys at the close.

What it can't reproduce (read results with this in mind):
- the 11:00/13:30/15:20 "2+ checkpoints" persistence rule (needs intraday bars)
- survivorship: the universe is TODAY's constituents - delisted/dropped names
  are absent, which flatters every variant
"""
from __future__ import annotations

import argparse
import logging
import pickle
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.metrics import max_drawdown_pct, trade_stats  # noqa: E402
from broker.mock_broker import MockBroker  # noqa: E402
from engine.trading_engine import TradingEngine  # noqa: E402
from risk.risk_manager import RiskManager  # noqa: E402
from strategies.close_strength import CloseStrengthStrategy  # noqa: E402
from strategies.screening_patterns import (  # noqa: E402
    FiftyTwoWeekHighBreakoutStrategy,
    RSIUptrendStrategy,
    StackedMAStrategy,
)
from strategies.trend_following import MACDStrategy, MovingAverageCrossStrategy  # noqa: E402
from utils.indicators import macd, rsi, sma  # noqa: E402

CACHE = ROOT / ".bt_cache" / "discovery_universe.pkl"
WARMUP_BARS = 260  # >= the longest pattern lookback (SMA200 / 52w = 252) so every variant starts the same day

# name -> the existing strategy class whose BUY signal defines the pattern
PATTERN_STRATEGIES = {
    "golden_cross": lambda: MovingAverageCrossStrategy(5, 20),
    "macd_cross": lambda: MACDStrategy(12, 26, 9),
    "rsi_uptrend": lambda: RSIUptrendStrategy(),
    "high_52w": lambda: FiftyTwoWeekHighBreakoutStrategy(),
    "stacked_ma": lambda: StackedMAStrategy(),
}


def load_data(history_days: int, refresh: bool = False) -> dict[str, pd.DataFrame]:
    """{symbol: OHLCV} for the KOSPI200+KOSDAQ150 universe, cached on disk
    (a full yfinance pull of ~350 tickers takes minutes)."""
    if CACHE.exists() and not refresh:
        return pickle.loads(CACHE.read_bytes())

    import yfinance as yf
    from screening.discover import get_universe

    symbols = list(get_universe())
    data: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), 40):
        chunk = symbols[i : i + 40]
        raw = yf.download(
            chunk, period="max", interval="1d", auto_adjust=False,
            group_by="ticker", threads=True, progress=False,
        )
        for symbol in chunk:
            try:
                df = raw[symbol].rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
            except KeyError:
                continue
            df = df.dropna(subset=["close"])
            df = df[df["volume"] > 0].tail(history_days)
            if len(df) > WARMUP_BARS + 50:
                data[symbol] = df
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_bytes(pickle.dumps(data))
    return data


def pattern_flags(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Per-bar boolean 'pattern says BUY on this bar' for one symbol - the
    vectorised twin of PATTERN_STRATEGIES[...].generate_signal (the strategies
    only look backwards, so evaluating every prefix == one pass over the whole
    series). tests/test_discovery_overlay.py checks the two agree; evaluating
    the classes per symbol per day would be ~2M calls.
    """
    c, v = df["close"], df["volume"]
    s5, s20, s50, s200 = sma(c, 5), sma(c, 20), sma(c, 50), sma(c, 200)
    macd_line, signal_line, _ = macd(c, 12, 26, 9)
    avg_vol = v.rolling(20).mean().shift()  # prior 20 days, excluding today
    flags = {
        "golden_cross": (s5.shift() <= s20.shift()) & (s5 > s20),
        "macd_cross": (macd_line.shift() <= signal_line.shift()) & (macd_line > signal_line),
        "rsi_uptrend": (rsi(c, 14) <= 30) & (c > s200),
        "high_52w": (c >= c.rolling(252).max() * (1 - 0.02)) & (avg_vol > 0) & (v >= avg_vol * 1.5),
        "stacked_ma": (c > s20) & (s20 > s50) & (s50 > s200),
    }
    for name, flag in flags.items():
        flag.iloc[: PATTERN_STRATEGIES[name]().min_bars_required() - 1] = False  # strategy HOLDs on short history
    return flags


def build_panels(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """close / volume / change% / each pattern flag as (date x symbol) frames."""
    frames = {"close": {}, "volume": {}, "chg": {}}
    flag_frames: dict[str, dict] = {name: {} for name in PATTERN_STRATEGIES}
    for symbol, df in data.items():
        frames["close"][symbol] = df["close"]
        frames["volume"][symbol] = df["volume"]
        frames["chg"][symbol] = df["close"].pct_change() * 100  # per-symbol, so a gap day isn't a fake move
        for name, flag in pattern_flags(df).items():
            flag_frames[name][symbol] = flag
    panels = {k: pd.DataFrame(v).sort_index() for k, v in frames.items()}
    for name, cols in flag_frames.items():
        panels[name] = pd.DataFrame(cols).reindex(panels["close"].index).fillna(False).astype(bool)
    return panels


def candidate_mask(panels: dict[str, pd.DataFrame], disc_cfg: dict, pattern: str | None, pool: str) -> pd.DataFrame:
    """(date x symbol) bool: who the live pipeline would have handed the
    engine that day. pool 'top5_then_pattern' = take the top-N movers, then
    keep the ones matching the pattern (the literal overlay); 'pattern_then_top5'
    = require the pattern first, then take the top-N of what's left.
    """
    chg, close, volume = panels["chg"], panels["close"], panels["volume"]
    eligible = (
        (close >= disc_cfg["min_price"])
        & (chg.abs() <= disc_cfg["max_change_pct"])
        & (close * volume >= disc_cfg["min_trading_value"])
    )
    n = disc_cfg["max_candidates"]

    def top_n(mask: pd.DataFrame) -> pd.DataFrame:
        return chg.where(mask).rank(axis=1, ascending=False, method="first") <= n

    if pattern is None:
        return top_n(eligible)
    if pool == "top5_then_pattern":
        return top_n(eligible) & panels[pattern]
    if pool == "pattern_then_top5":
        return top_n(eligible & panels[pattern])
    raise ValueError(f"unknown pool {pool!r}")


def simulate(config: dict, data: dict[str, pd.DataFrame], candidates: pd.DataFrame, start: int) -> dict:
    """Replays candidates day by day through the live TradingEngine."""
    import main as app  # build_regime_filter/build_volume_filter - reuse, don't re-implement

    disc, risk, costs = config["discovery"], config["risk"], config.get("costs", {})
    broker = MockBroker(
        disc["seed_capital"], costs.get("commission_pct", 0.0), costs.get("sell_tax_pct", 0.0),
        costs.get("slippage_pct", 0.0),
    )
    risk_manager = RiskManager(
        seed_capital=disc["seed_capital"], stop_loss_pct=risk["stop_loss_pct"],
        take_profit_pct=risk["take_profit_pct"], position_size_pct=disc["position_size_pct"],
        daily_max_loss_pct=risk["daily_max_loss_pct"],
    )
    regime = app.build_regime_filter(config)
    engine = TradingEngine(
        broker=broker, data_feed=None,
        strategies=[CloseStrengthStrategy(disc["close_strength_ma_period"], disc["close_strength_position_pct"])],
        risk_manager=risk_manager, watchlist=[], regime_filter=regime, entry_mode="any",
        volume_filter=app.build_volume_filter(config), notify=False,
    )
    min_bars = max(disc["close_strength_ma_period"] + 1, regime.adx_period * 2 + 5 if regime else 0)

    curve = []
    for day in candidates.index[start:]:
        todays = candidates.loc[day]
        symbols = set(todays.index[todays]) | set(broker.get_positions())
        windows = {}
        for symbol in symbols:
            df = data.get(symbol)
            if df is None:
                continue
            i = df.index.searchsorted(day, side="right")
            if i >= min_bars and df.index[i - 1] == day:  # no bar today (halt) -> leave the position alone
                windows[symbol] = df.iloc[:i]
        equity = engine.run_once(as_of=day.date(), precomputed_windows=windows)
        curve.append((day, equity))

    equity_curve = pd.Series(dict(curve))
    stats = trade_stats(broker.order_log)
    seed = disc["seed_capital"]
    return {
        "return_pct": (equity_curve.iloc[-1] / seed - 1) * 100,
        "mdd_pct": max_drawdown_pct(equity_curve),
        "round_trips": stats["num_round_trips"],
        "win_rate_pct": stats["win_rate_pct"],
        "avg_candidates_per_day": float(candidates.iloc[start:].sum(axis=1).mean()),
        "days": len(equity_curve),
    }


def main() -> None:
    import yaml

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="re-download price history")
    parser.add_argument("--pools", default="top5_then_pattern,pattern_then_top5")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = parser.parse_args()

    logging.disable(logging.WARNING)  # MockBroker/engine warn on every skipped order
    config = yaml.safe_load(open(args.config, encoding="utf-8"))
    data = load_data(config["backtest"]["history_days"], args.refresh)
    panels = build_panels(data)
    start = WARMUP_BARS
    first, last = panels["close"].index[start], panels["close"].index[-1]
    print(f"universe={len(data)} symbols, {first.date()} -> {last.date()} ({len(panels['close']) - start} days)\n")

    runs = [("baseline (no pattern)", None, "-")]
    for pool in args.pools.split(","):
        runs += [(name, name, pool) for name in PATTERN_STRATEGIES]

    rows = []
    for label, pattern, pool in runs:
        mask = candidate_mask(panels, config["discovery"], pattern, pool)
        result = simulate(config, data, mask, start)
        rows.append({"pattern": label, "pool": pool, **result})
        print(f"{label:<22} {pool:<18} ret={result['return_pct']:+7.1f}% mdd={result['mdd_pct']:6.1f}% "
              f"trades={result['round_trips']:4d} win={result['win_rate_pct']:4.0f}% "
              f"cand/day={result['avg_candidates_per_day']:.2f}", flush=True)
    pd.DataFrame(rows).to_csv(ROOT / ".bt_cache" / "discovery_overlay_results.csv", index=False)


if __name__ == "__main__":
    main()
