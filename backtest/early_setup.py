"""Backtest: stocks that show SEVERAL bullish chart signals but haven't risen
yet ("미리 진입"). Do more signals really mean better forward returns?

Run: python -m backtest.early_setup  (reuses the price cache from
backtest.discovery_overlay; needs the same env as that module)

Setup definition (all thresholds are inputs, not tuned on results):
- not yet risen: today's change within +-max_chg% AND 5-day return <= max_ret5%
  (plus the live sleeve's liquidity filters: price/거래대금)
- signals (score = how many hold today), built from the conditions already
  used in this repo; event-type ones count if they fired within the last
  `recent` bars, because a one-day cross is too rare to ever coincide:
    golden_cross(5/20), macd_cross, rsi_uptrend, high_52w   (event, recent)
    stacked_ma(정배열), close_strength BUY, volume surge     (state, today)
Two views: (1) event study - forward returns / +18%-before--9% hit rate by
score; (2) portfolio simulation through the live TradingEngine.
Same blind spots as discovery_overlay: today's index constituents (survivor-
ship), entry at the close, whale_flow not included (needs per-day investor
data for ~350 tickers).
"""
from __future__ import annotations

import argparse
import logging
import pickle

import numpy as np
import pandas as pd

from backtest.discovery_overlay import ROOT, WARMUP_BARS, load_data, pattern_flags
from backtest.metrics import max_drawdown_pct, trade_stats
from broker.mock_broker import MockBroker
from engine.trading_engine import TradingEngine
from risk.risk_manager import RiskManager
from strategies.base import Signal, Strategy, StrategyCategory, StrategySignal
from strategies.close_strength import CloseStrengthStrategy
from utils.indicators import sma

SIGNALS = ["golden_cross", "macd_cross", "rsi_uptrend", "high_52w", "stacked_ma", "close_strength", "volume_surge"]
EVENT_SIGNALS = ["golden_cross", "macd_cross", "rsi_uptrend", "high_52w"]
WHALE = "whale_flow"
WHALE_CACHE = ROOT / ".bt_cache" / "investor_flow.pkl"
# NO downloader on purpose: a bulk pykrx pull of this universe got the PC's IP banned by KRX for a day on
# 2026-10-02 (automated bulk collection is against its terms) and took the live whale_flow sleeves down
# with it. Provide the cache from an official KRX source (Open API / screen download) if needed.


def whale_series(df: pd.DataFrame, flow: pd.DataFrame, window: int = 5, threshold: float = 0.05) -> tuple[pd.Series, pd.Series]:
    """(ratio, flag) as WhaleFlowStrategy computes them, but anchored like live:
    KRX confirms a day's flows about a day late, so the signal on day t only
    sees flows through t-1 (shift(1))."""
    net = (flow["institutional_net"] + flow["foreign_net"]).reindex(df.index)
    turnover = df["close"] * df["volume"]
    ratio = net.shift(1).rolling(window).sum() / turnover.shift(1).rolling(window).sum()
    return ratio, ratio >= threshold


def signal_flags(df: pd.DataFrame, recent: int) -> dict[str, pd.Series]:
    """Per-bar booleans for the 7 signals of one symbol (causal)."""
    flags = pattern_flags(df)
    out = {
        name: (flags[name].rolling(recent).max() > 0) if name in EVENT_SIGNALS else flags[name]
        for name in ("golden_cross", "macd_cross", "rsi_uptrend", "high_52w", "stacked_ma")
    }
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    rng = h - l
    # CloseStrengthStrategy: close in the top 20% of the day's range and above the 20-bar mean
    cs = (rng > 0) & ((c - l) / rng.where(rng > 0) >= 0.8) & (c > sma(c, 20))
    cs.iloc[:20] = False  # strategy needs 21 bars
    avg_vol = v.rolling(20).mean().shift()
    surge = (avg_vol > 0) & (v >= avg_vol * 1.5)  # VolumeFilter(20, 1.5)
    surge.iloc[:20] = False
    out["close_strength"], out["volume_surge"] = cs, surge
    return {k: s.eq(True) for k, s in out.items()}  # NaN (warm-up) -> False


def build_panels(data: dict[str, pd.DataFrame], recent: int, flows: dict[str, pd.DataFrame] | None = None) -> dict:
    cols: dict[str, dict] = {
        k: {} for k in ["close", "volume", "chg", "ret5", "vol_ratio", "near_high20", "squeeze", "above50", *SIGNALS, WHALE, "whale_ratio"]
    }
    for symbol, df in data.items():
        c, v = df["close"], df["volume"]
        cols["close"][symbol], cols["volume"][symbol] = c, v
        cols["chg"][symbol] = c.pct_change() * 100
        cols["ret5"][symbol] = c.pct_change(5) * 100
        cols["vol_ratio"][symbol] = v / v.rolling(20).mean().shift()
        cols["near_high20"][symbol] = c / c.rolling(20).max()  # 1.0 = closing at a fresh 20-day high
        bandwidth = c.rolling(20).std() / sma(c, 20)
        cols["squeeze"][symbol] = bandwidth <= bandwidth.rolling(120).quantile(0.25)  # volatility compressed vs own history
        cols["above50"][symbol] = c > sma(c, 50)
        for name, flag in signal_flags(df, recent).items():
            cols[name][symbol] = flag
        if flows is not None and symbol in flows:
            ratio, flag = whale_series(df, flows[symbol])
            cols["whale_ratio"][symbol], cols[WHALE][symbol] = ratio, flag
    panels = {k: pd.DataFrame(v).sort_index() for k, v in cols.items()}
    index = panels["close"].index
    for name in [*SIGNALS, "squeeze", "above50"]:
        panels[name] = panels[name].reindex(index).eq(True)  # missing (symbol has no bar that day) -> False
    names = [*SIGNALS, WHALE] if flows is not None else SIGNALS
    panels[WHALE] = panels[WHALE].reindex(index).eq(True)
    panels["whale_ratio"] = panels["whale_ratio"].reindex(index)
    panels["score"] = sum(panels[name].astype(int) for name in names)  # whale counts only when its data was loaded
    return panels


def setup_mask(panels: dict, cfg: dict) -> pd.DataFrame:
    """Liquid + not-yet-risen stock-days (before any signal requirement)."""
    d = cfg["discovery"]
    return (
        (panels["close"] >= d["min_price"])
        & (panels["close"] * panels["volume"] >= d["min_trading_value"])
        & (panels["chg"].abs() <= cfg["early"]["max_chg_pct"])
        & (panels["ret5"] <= cfg["early"]["max_ret5_pct"])
    )


def _forward_arrays(close: pd.DataFrame, horizon: int, tp: float, sl: float) -> dict:
    c = close.to_numpy()
    day_tp = np.full(c.shape, np.inf)
    day_sl = np.full(c.shape, np.inf)

    def fwd(h):
        out = np.full(c.shape, np.nan)
        out[:-h] = c[h:] / c[:-h] - 1
        return out

    for h in range(1, horizon + 1):
        f = fwd(h)
        day_tp = np.where((f >= tp) & np.isinf(day_tp), h, day_tp)
        day_sl = np.where((f <= -sl) & np.isinf(day_sl), h, day_sl)
    complete = np.zeros(c.shape, bool)
    complete[: len(c) - horizon] = True  # a full forward window exists
    return {
        "f5": fwd(5) * 100, "f10": fwd(10) * 100, "f20": fwd(20) * 100,
        "outcome": np.where(day_tp < day_sl, "tp", np.where(day_sl < day_tp, "sl", "neither")),
        "valid": complete & np.isfinite(c),
    }


def _row_mean(x: np.ndarray, m: np.ndarray, min_n: int = 5) -> np.ndarray:
    n = m.sum(axis=1)
    return np.where(n >= min_n, np.where(m, x, 0).sum(axis=1) / np.maximum(n, 1), np.nan)


def excess_t(f10: np.ndarray, group: np.ndarray, base: np.ndarray, horizon: int = 10, min_n: int = 5) -> tuple[float, float]:
    """(mean daily excess of group over base in %, t-stat). Day-by-day paired
    difference of cross-sectional means; 10-day forward windows overlap, so
    the effective number of independent days is days/horizon - conservative
    on purpose (a naive stock-day t-stat would be wildly overconfident).
    """
    d = _row_mean(f10, group, min_n) - _row_mean(f10, base)
    d = d[np.isfinite(d)]
    if len(d) < horizon * 3:
        return float("nan"), float("nan")
    return float(d.mean()), float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d) / horizon)))


def forward_table(panels: dict, buckets: dict[str, pd.DataFrame], horizon: int, tp: float, sl: float,
                  base: pd.DataFrame | None = None) -> pd.DataFrame:
    """Forward stats per bucket of stock-days. If `base` is given, adds the
    daily-paired excess of each bucket's 10d return over that reference set."""
    arr = _forward_arrays(panels["close"], horizon, tp, sl)
    warm = np.ones(arr["valid"].shape, bool)
    warm[:WARMUP_BARS] = False
    ok = arr["valid"] & warm
    base_m = None if base is None else base.to_numpy() & ok
    rows = []
    for label, mask in buckets.items():
        m = mask.to_numpy() & ok
        if m.sum() == 0:
            continue
        o = arr["outcome"][m]
        row = {
            "bucket": label, "n": int(m.sum()),
            "fwd5%": np.nanmean(arr["f5"][m]), "fwd10%": np.nanmean(arr["f10"][m]), "fwd20%": np.nanmean(arr["f20"][m]),
            "win10%": float((arr["f10"][m] > 0).mean() * 100),
            "tp_first%": float((o == "tp").mean() * 100), "sl_first%": float((o == "sl").mean() * 100),
        }
        if base_m is not None:
            # rare signals have <5 hits on most days, so a daily cross-section is empty - accept 1
            row["excess10%"], row["t"] = excess_t(arr["f10"], m, base_m, min_n=5 if m.sum() > 3000 else 1)
        rows.append(row)
    return pd.DataFrame(rows)


def liquid_mask(panels: dict, cfg: dict) -> pd.DataFrame:
    d = cfg["discovery"]
    return (panels["close"] >= d["min_price"]) & (panels["close"] * panels["volume"] >= d["min_trading_value"])


def compression_mask(panels: dict, cfg: dict) -> pd.DataFrame:
    """Alternative 'not yet risen' definition - a coiled spring just below a
    breakout: within `near_pct` of (but still below) the 20-day high, volatility
    squeezed vs its own last 120 days, above the 50-day mean, and no big move today.
    """
    near = panels["near_high20"]
    return (
        liquid_mask(panels, cfg)
        & (near >= 1 - cfg["early"]["near_pct"] / 100) & (near < 1.0)
        & panels["squeeze"] & panels["above50"]
        & (panels["chg"] <= cfg["early"]["max_chg_pct"])
    )


def event_study(panels: dict, mask: pd.DataFrame, horizon: int, tp: float, sl: float) -> pd.DataFrame:
    """Forward stats by signal-count within `mask`."""
    buckets = {f"score={k}": mask & (panels["score"] == k) for k in range(0, 5)}
    buckets["score>=5"] = mask & (panels["score"] >= 5)
    buckets["ALL setups"] = mask
    return forward_table(panels, buckets, horizon, tp, sl)


def per_signal_study(panels: dict, mask: pd.DataFrame, horizon: int, tp: float, sl: float, names: list[str] = SIGNALS) -> pd.DataFrame:
    """Each signal on its own: setups WITH it vs setups WITHOUT it."""
    buckets = {f"{name} = True": mask & panels[name] for name in names}
    buckets["(no signal at all)"] = mask & (panels["score"] == 0)
    buckets["ALL setups"] = mask
    table = forward_table(panels, buckets, horizon, tp, sl, base=mask)
    # excess is vs ALL setups; for the signal rows "without it" matters more, so also show it directly
    without = forward_table(panels, {f"{name} = False": mask & ~panels[name] for name in names}, horizon, tp, sl)
    return pd.concat([table, without], ignore_index=True)


def by_year(panels: dict, mask: pd.DataFrame, min_score: int) -> pd.DataFrame:
    close = panels["close"]
    fwd10 = (close.shift(-10) / close - 1) * 100
    sel = mask & (panels["score"] >= min_score)
    sel.iloc[:WARMUP_BARS] = False
    rows = []
    base_mask = mask.copy()
    base_mask.iloc[:WARMUP_BARS] = False
    for year, idx in close.groupby(close.index.year).groups.items():
        vals, base = fwd10.loc[idx].where(sel.loc[idx]).stack(), fwd10.loc[idx].where(base_mask.loc[idx]).stack()
        if len(vals):
            rows.append({"year": year, "n": len(vals), f"fwd10% (score>={min_score})": vals.mean(), "fwd10% (all setups)": base.mean()})
    return pd.DataFrame(rows)


class SetupEntryStrategy(Strategy):
    """BUY when the day's pre-computed candidate set contains the symbol; exits
    mirror the discovery sleeve (stop/take-profit in RiskManager plus the
    close_strength weak-close SELL).
    """

    name = "early_setup"
    category = StrategyCategory.TREND_FOLLOWING

    def __init__(self, candidates: pd.DataFrame):
        self.candidates = candidates
        self._exit = CloseStrengthStrategy(20, 0.8)

    def min_bars_required(self) -> int:
        return 21

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        day, price = ohlcv.index[-1], float(ohlcv["close"].iloc[-1])
        if self.candidates.at[day, symbol]:
            return StrategySignal(symbol, Signal.BUY, self.name, "multi-signal setup", price)
        exit_signal = self._exit.generate_signal(symbol, ohlcv)
        return exit_signal if exit_signal.signal == Signal.SELL else self._hold(symbol, "no setup")


def pick_candidates(panels: dict, mask: pd.DataFrame, min_score: int, n: int, rng: np.random.Generator | None,
                    rank_key: pd.DataFrame | None = None) -> pd.DataFrame:
    """Top-n setups per day by (score, volume ratio); rng given = random pick (control)."""
    ok = mask & (panels["score"] >= min_score)
    if rank_key is not None:
        key = rank_key
    elif rng is None:
        key = panels["score"] * 1000 + panels["vol_ratio"].clip(upper=999).fillna(0)
    else:
        key = pd.DataFrame(rng.random(ok.shape), ok.index, ok.columns)
    return key.where(ok).rank(axis=1, ascending=False, method="first") <= n


def simulate(cfg: dict, data: dict, candidates: pd.DataFrame) -> dict:
    d, risk, costs = cfg["discovery"], cfg["risk"], cfg.get("costs", {})
    broker = MockBroker(d["seed_capital"], costs.get("commission_pct", 0.0), costs.get("sell_tax_pct", 0.0), costs.get("slippage_pct", 0.0))
    rm = RiskManager(d["seed_capital"], risk["stop_loss_pct"], risk["take_profit_pct"], d["position_size_pct"], risk["daily_max_loss_pct"])
    # no regime/volume filter: ADX>=25 means "already trending", the opposite of an early setup,
    # and a volume surge is one of the signals already
    engine = TradingEngine(broker, None, [SetupEntryStrategy(candidates)], rm, [], entry_mode="any", notify=False)
    curve = []
    for day in candidates.index[WARMUP_BARS:]:
        row = candidates.loc[day]
        windows = {}
        for symbol in set(row.index[row]) | set(broker.get_positions()):
            df = data.get(symbol)
            if df is None:
                continue
            i = df.index.searchsorted(day, side="right")
            if i >= 21 and df.index[i - 1] == day:
                windows[symbol] = df.iloc[:i]
        curve.append((day, engine.run_once(as_of=day.date(), precomputed_windows=windows)))
    eq = pd.Series(dict(curve))
    stats = trade_stats(broker.order_log)
    return {
        "return%": (eq.iloc[-1] / d["seed_capital"] - 1) * 100, "mdd%": max_drawdown_pct(eq),
        "trades": stats["num_round_trips"], "win%": stats["win_rate_pct"],
        "cand/day": float(candidates.iloc[WARMUP_BARS:].sum(axis=1).mean()),
    }


def _sim_table(cfg: dict, data: dict, panels: dict, mask: pd.DataFrame, scores: list[int], label: str) -> pd.DataFrame:
    n = cfg["discovery"]["max_candidates"]
    runs = [(f"{label} score>={k}", pick_candidates(panels, mask, k, n, None)) for k in scores]
    runs.append((f"{label} RANDOM (control)", pick_candidates(panels, mask, 0, n, np.random.default_rng(0))))
    return pd.DataFrame([{"variant": name, **simulate(cfg, data, cand)} for name, cand in runs])


def main() -> None:
    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument("--step", choices=["score", "signals", "compression", "whale", "all"], default="all")
    parser.add_argument("--recent", type=int, default=3, help="event signals count if fired within this many bars")
    parser.add_argument("--max-chg", type=float, default=3.0)
    parser.add_argument("--max-ret5", type=float, default=5.0)
    parser.add_argument("--near-pct", type=float, default=3.0, help="compression: within this %% below the 20d high")
    parser.add_argument("--scores", default="2,3,4")
    args = parser.parse_args()

    logging.disable(logging.WARNING)
    pd.set_option("display.width", 220, "display.float_format", lambda x: f"{x:,.2f}")
    cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
    cfg["early"] = {"max_chg_pct": args.max_chg, "max_ret5_pct": args.max_ret5, "near_pct": args.near_pct}
    data = load_data(cfg["backtest"]["history_days"])
    flows = None
    if args.step in ("whale", "all") and WHALE_CACHE.exists():
        flows = pickle.loads(WHALE_CACHE.read_bytes())
    panels = build_panels(data, args.recent, flows)
    tp, sl = cfg["risk"]["take_profit_pct"], cfg["risk"]["stop_loss_pct"]
    scores = [int(k) for k in args.scores.split(",")]
    print(f"universe={len(data)}  window={panels['close'].index[WARMUP_BARS].date()} -> {panels['close'].index[-1].date()}  "
          f"(TP-first break-even ~{sl / (tp + sl):.0%}; t = paired daily excess, effective days/10)\n")

    mask = setup_mask(panels, cfg)
    if args.step in ("score", "all"):
        print(f"== SETUP A: |chg|<={args.max_chg}% & 5d<={args.max_ret5}% - by signal count ==")
        print(event_study(panels, mask, 20, tp, sl).to_string(index=False))
        print("\n== SETUP A: portfolio simulation ==")
        print(_sim_table(cfg, data, panels, mask, scores, "A").to_string(index=False))
    if args.step in ("signals", "all"):
        print("\n== STEP 1: each signal on its own within setup A (excess10% vs ALL setups) ==")
        print(per_signal_study(panels, mask, 20, tp, sl).to_string(index=False))
    if args.step in ("compression", "all"):
        comp = compression_mask(panels, cfg)
        liquid = liquid_mask(panels, cfg)
        print(f"\n== STEP 2: compression setup (<= {args.near_pct}% below 20d high, squeezed, above 50d, no big move) ==")
        buckets = {"all liquid stock-days (market)": liquid, "setup A (not yet risen)": mask, "compression setup": comp}
        buckets.update({f"compression & score>={k}": comp & (panels["score"] >= k) for k in (1, 2, 3)})
        print(forward_table(panels, buckets, 20, tp, sl, base=liquid).to_string(index=False))
        print("\n-- compression: simulation --")
        print(_sim_table(cfg, data, panels, comp, [1, 2], "compression").to_string(index=False))
    if args.step in ("whale", "all") and flows is not None:
        liquid = liquid_mask(panels, cfg)
        comp = compression_mask(panels, cfg)
        have = panels[WHALE].any(axis=0).sum()
        print("")
        print(f"== STEP 3: whale_flow (5d foreign+institution net-buy / turnover >= 5%, lagged 1 day), data for {have}/{len(data)} symbols ==")
        print(per_signal_study(panels, mask, 20, tp, sl, names=[WHALE]).to_string(index=False))
        buckets = {
            "all liquid stock-days (market)": liquid,
            "setup A": mask,
            "setup A & whale": mask & panels[WHALE],
            "compression": comp,
            "compression & whale": comp & panels[WHALE],
            "whale only (any price action)": liquid & panels[WHALE],
        }
        print("")
        print("-- whale vs market baseline --")
        print(forward_table(panels, buckets, 20, tp, sl, base=liquid).to_string(index=False))
        print("")
        print("-- signal count now INCLUDING whale (setup A) --")
        print(event_study(panels, mask, 20, tp, sl).to_string(index=False))
        print("")
        print("-- simulation (candidates ranked by whale net-buy ratio when whale-gated) --")
        n = cfg["discovery"]["max_candidates"]
        ratio = panels["whale_ratio"]
        runs = [
            ("setup A & whale (by ratio)", pick_candidates(panels, mask & panels[WHALE], 0, n, None, rank_key=ratio)),
            ("compression & whale (by ratio)", pick_candidates(panels, comp & panels[WHALE], 0, n, None, rank_key=ratio)),
            ("setup A score>=2 incl. whale", pick_candidates(panels, mask, 2, n, None)),
            ("setup A RANDOM (control)", pick_candidates(panels, mask, 0, n, np.random.default_rng(0))),
        ]
        print(pd.DataFrame([{"variant": name, **simulate(cfg, data, cand)} for name, cand in runs]).to_string(index=False))


if __name__ == "__main__":
    main()
