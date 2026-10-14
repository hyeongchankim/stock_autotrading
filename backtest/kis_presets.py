"""Re-implementation (NOT execution) of the 10 preset strategies of koreainvestment/open-trading-api
(strategy_builder/strategy/strategy_01..10) on this repo's price cache and cost model.
Rules/defaults are copied from those files; the API-only inputs are approximated:
  #3 52w high: KIS gives `w52_high` from the quote API; here close > max(high) of the prior 252 bars.
  #6 breakout-fail: the file's window is required_days(28) - 3 = the 25 bars before the last 3 (not 20).
Signals are known at the close of day t; entries are the NEXT open (no look-ahead).

Two views per strategy:
  1. signal quality - forward return of every BUY (net of our cost) / SELL (gross; a working SELL should
     show NEGATIVE excess) vs the liquid-universe average over the same horizon, daily-paired t-stat.
  2. portfolio - BUY entries, exit at the strategy's own SELL signal or after MAX_HOLD days, <=SLOTS positions,
     equal 1/SLOTS weight, idle cash earns 0. #3/#7/#8 have no SELL rule, so they only exit on the time cap.

Run: python -m backtest.kis_presets   (needs .bt_cache from backtest.discovery_overlay.load_data)
Caveat: universe = today's KOSPI200+KOSDAQ150 (survivorship); the official backtester (Lean) is not run here.
"""
from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd

from backtest.discovery_overlay import ROOT, WARMUP_BARS, load_data
from backtest.early_setup import excess_t
from backtest.github_strategies import build_panels
from backtest.metrics import max_drawdown_pct

HORIZONS = (1, 5, 10, 20)
SLOTS, MAX_HOLD = 10, 20


def _streak(cond: pd.DataFrame) -> pd.DataFrame:
    """Length of the current run of True per column."""
    c = cond.astype(int).cumsum()
    return c - c.where(~cond).ffill().fillna(0)


def signals(p: dict) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    """name -> (buy mask, sell mask), evaluated at each day's close."""
    o, h, l, c = p["open"], p["high"], p["low"], p["close"]
    sma = lambda n: c.rolling(n).mean()  # noqa: E731
    f = pd.DataFrame(False, index=c.index, columns=c.columns)
    s5, s20 = sma(5), sma(20)
    up, dn = _streak(c.diff() > 0), _streak(c.diff() < 0)
    r60 = c.pct_change(60)
    recent_high, prev_high = h.rolling(3).max(), h.shift(3).rolling(25).max()
    vol10 = c.pct_change().rolling(10).std()
    chg = c.pct_change() * 100
    dev5 = (c / sma(5) - 1) * 100
    ma60 = sma(60)
    return {
        "01 golden cross 5/20": ((s5.shift() < s20.shift()) & (s5 > s20), (s5.shift() > s20.shift()) & (s5 < s20)),
        "02 momentum 60d +30/-20%": (r60 >= 0.30, r60 <= -0.20),
        "03 52w high": (c > h.shift().rolling(252).max(), f),
        "04 consecutive 5d": (up >= 5, dn >= 5),
        "05 disparity 90/110": (c / s20 * 100 < 90, c / s20 * 100 > 110),
        "06 breakout fail (sell-only)": (f, (recent_high > prev_high) & (c / recent_high - 1 <= -0.03)),
        "07 strong close >=0.8": ((c - l) / (h - l) >= 0.8, f),
        "08 vol expansion +3%": ((vol10 <= vol10.rolling(10).min() * 1.1) & (chg >= 3.0), f),
        "09 mean reversion 5d +-3%": (dev5 <= -3.0, dev5 >= 3.0),
        "10 trend filter MA60": ((c > ma60) & (c > c.shift()), (c <= ma60) & (c <= c.shift())),
    }


def forward(p: dict, cost: float) -> dict[int, pd.DataFrame]:
    """Buy next open, sell the close h trading days after the signal day (h=1: next open->next close)."""
    entry = p["open"].shift(-1)
    return {h: p["close"].shift(-h) / entry - 1 - cost for h in HORIZONS}


def signal_table(sigs: dict, fwd: dict, liquid: pd.DataFrame, start: int, cost: float) -> pd.DataFrame:
    ok = np.zeros(liquid.shape, bool)
    ok[start:] = True
    rows = []
    for name, (buy, sell) in sigs.items():
        for side, mask in (("BUY", buy), ("SELL", sell)):
            m0 = (mask & liquid).to_numpy() & ok
            if m0.sum() < 30:
                continue
            for h, net in fwd.items():
                arr = (net if side == "BUY" else net + cost).to_numpy()  # SELL shown gross
                base = liquid.to_numpy() & ok & np.isfinite(arr)
                m = m0 & np.isfinite(arr)
                d, t = excess_t(arr * 100, m, base, horizon=h, min_n=1)
                rows.append({"strategy": name, "side": side, "h": h, "n": int(m.sum()), "mean%": arr[m].mean() * 100,
                             "win%": (arr[m] > 0).mean() * 100, "excess%": d, "t": t})
    return pd.DataFrame(rows)


def simulate(p: dict, buy: pd.DataFrame, sell: pd.DataFrame, liquid: pd.DataFrame, cost: float, start: int,
             seed: int = 0) -> tuple[pd.Series, list[float]]:
    """Long-only; signals at close t act at open t+1. Returns (daily return series, per-trade net returns)."""
    O, C = p["open"].to_numpy(), p["close"].to_numpy()
    B, S, L = buy.to_numpy(), sell.to_numpy(), liquid.to_numpy()
    T, N = C.shape
    rng = np.random.default_rng(seed)
    held = np.zeros(N, bool)
    age = np.zeros(N, int)
    cum = np.ones(N)  # compounded gross growth of the open trade, for the per-trade stat
    entry_p, exit_p = np.zeros(N, bool), np.zeros(N, bool)
    day_ret, trades = np.zeros(T), []
    for t in range(1, T):
        ret = np.zeros(N)
        ex = exit_p & held
        ret[ex] = O[t, ex] / C[t - 1, ex] - 1 - cost  # exit at the open, pay the round trip once
        stay = held & ~ex
        ret[stay] = C[t, stay] / C[t - 1, stay] - 1
        cum[held] *= 1 + np.nan_to_num(ret[held])
        trades += list(cum[ex] - 1)
        held = stay
        free = SLOTS - int(held.sum())
        cand = np.flatnonzero(entry_p & ~held & np.isfinite(O[t]) & np.isfinite(C[t]))
        if free > 0 and len(cand):
            pick = rng.permutation(cand)[:free]
            held[pick] = True
            age[pick], cum[pick] = 0, 1.0
            ret[pick] = C[t, pick] / O[t, pick] - 1
            cum[pick] *= 1 + ret[pick]
        age[held] += 1
        day_ret[t] = np.nansum(ret) / SLOTS
        exit_p = held & (S[t] | (age >= MAX_HOLD))
        entry_p = B[t] & L[t]
    return pd.Series(day_ret, index=p["close"].index).iloc[start:], trades


def stats(day_ret: pd.Series, trades: list[float]) -> dict:
    eq = (1 + day_ret).cumprod()
    years = len(day_ret) / 252
    half = len(eq) // 2
    sd = day_ret.std()
    return {"return%": (eq.iloc[-1] - 1) * 100, "CAGR%": (eq.iloc[-1] ** (1 / years) - 1) * 100,
            "mdd%": max_drawdown_pct(eq), "sharpe": day_ret.mean() / sd * np.sqrt(252) if sd > 0 else float("nan"),
            "trades": len(trades), "win%": float(np.mean(np.array(trades) > 0) * 100) if trades else float("nan"),
            "avg_trade%": float(np.mean(trades) * 100) if trades else float("nan"),
            "H1%": (eq.iloc[half - 1] - 1) * 100, "H2%": (eq.iloc[-1] / eq.iloc[half - 1] - 1) * 100}


def main() -> None:
    import yaml

    logging.disable(logging.WARNING)
    warnings.simplefilter("ignore", FutureWarning)  # pandas pct_change/fillna deprecations, behaviour intended
    pd.set_option("display.width", 230, "display.float_format", lambda x: f"{x:,.2f}")
    cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
    cost = 2 * cfg["costs"]["commission_pct"] / 100 + cfg["costs"]["sell_tax_pct"] / 100
    p = build_panels(load_data(cfg["backtest"]["history_days"]))
    start = WARMUP_BARS
    liquid = (p["close"] >= cfg["discovery"]["min_price"]) & (p["value"] >= cfg["discovery"]["min_trading_value"])
    sigs = signals(p)
    print(f"universe={p['close'].shape[1]}  {p['close'].index[start].date()} -> {p['close'].index[-1].date()}  "
          f"round-trip cost {cost * 100:.2f}%  liquid = close>={cfg['discovery']['min_price']:,} & value>={cfg['discovery']['min_trading_value']:,}")

    print("\n== 1. SIGNAL QUALITY (BUY net of cost; SELL gross, working SELL => negative excess) ==")
    print(signal_table(sigs, forward(p, cost), liquid, start, cost).to_string(index=False))

    print(f"\n== 2. PORTFOLIO (<= {SLOTS} positions, exit on own SELL or {MAX_HOLD}d, idle cash 0) ==")
    rows = []
    bench = p["close"].pct_change().where(liquid.shift().fillna(False)).mean(axis=1).fillna(0).iloc[start:]
    rows.append({"strategy": "benchmark: liquid universe, daily EW", **stats(bench, [])})
    for name, (buy, sell) in sigs.items():
        if not buy.to_numpy().any():
            continue
        rows.append({"strategy": name, **stats(*simulate(p, buy, sell, liquid, cost, start))})
    print(pd.DataFrame(rows).to_string(index=False))


def _selfcheck() -> None:
    idx = pd.date_range("2024-01-01", periods=12)
    close = pd.DataFrame({"A": [10, 11, 12, 13, 14, 15, 14, 13, 12, 11, 10, 9.0]}, idx)
    assert _streak(close.diff() > 0)["A"].tolist()[1:7] == [1, 2, 3, 4, 5, 0]
    assert _streak(close.diff() < 0)["A"].iloc[-1] == 6
    p = {k: close.copy() for k in ("open", "high", "low", "close")}
    buy = pd.DataFrame(False, idx, close.columns)
    sell = buy.copy()
    buy.iloc[2], sell.iloc[5] = True, True  # signal day 2 -> buy open d3 (13), signal day 5 -> sell open d6 (14)
    liq = pd.DataFrame(True, idx, close.columns)
    r, tr = simulate(p, buy, sell, liq, 0.0, 0)
    assert abs(tr[0] - (14 / 13 - 1)) < 1e-9, tr
    assert abs(r.sum() * SLOTS - ((14 / 13 - 1) + (15 / 14 - 1) + (14 / 15 - 1))) < 1e-9, r.sum()


if __name__ == "__main__":
    _selfcheck()
    main()
