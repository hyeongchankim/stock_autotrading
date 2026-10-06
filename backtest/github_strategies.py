"""Re-implementation (NOT execution) of three public GitHub strategies on this repo's
price cache, with this repo's cost model, so they can be compared on equal terms.
Their code is never downloaded or run - only the documented RULES are re-coded here:

A  jhg-park-0304/kr-equity-mean-reversion  overnight gap-down mean reversion
   gap = open/prev_close-1 in a band, weak 20d momentum, prev-day vol ratio <= 2;
   rank by -z(gap) - z(mom20), buy top 5 at the OPEN, exit next OPEN (small tier:
   exit at the close instead if close < open*0.98). Tier filters from its example config.
B  tpwns1903/krx-volume-pattern-backtester  "거래량 유입 후 감소"
   day1 volume > m x prior-20d mean, day2 volume > day1, then 1-3 declining days; buy the
   NEXT open. The published signal day is the LAST day of the decline run, which is only
   known afterwards (look-ahead) - here each decline-run length (1/2/3 days) is its own
   causal signal, known at that day's close.
C  neo9999999999/neo-stock-py  종가 베팅 5대 조건 (+ candle/disparity/market extras)
   60d new high, +5..25% day, trading value >= 50bn (KOSDAQ 20bn), volume >= 3x 60d mean,
   close >= 0.97 x high; extras: body >= 70% of range, 100 <= close/SMA5 <= 115, market >= 5d MA.

Run: python -m backtest.github_strategies   (uses the discovery_overlay price cache, no KRX/KIS)

Caveats: our universe is today's 347 KOSPI200+KOSDAQ150 names (large/mid caps, survivorship);
A claims ~2,300 tickers and B all of KOSDAQ - their small-cap edge, if any, is NOT testable here
(a whole-market download would repeat the 2026-10-02 KRX ban). C's exit rule is not documented,
so several horizons are shown. Entries assume exact open/close fills.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from backtest.discovery_overlay import ROOT, WARMUP_BARS, load_data
from backtest.early_setup import excess_t
from backtest.metrics import max_drawdown_pct

TIERS = {  # from jhg-park-0304/kr-equity-mean-reversion config/strategy.example.yaml
    "small-tier rules (loose)": dict(gap=(-0.15, -0.03), mom_max=0.0, prev_tv_min=0.0, min_price=500, stop=0.02),
    "medium-tier rules": dict(gap=(-0.10, -0.04), mom_max=-0.05, prev_tv_min=3e10, min_price=0, stop=0.0),
    "large-tier rules": dict(gap=(-0.10, -0.03), mom_max=-0.10, prev_tv_min=3e10, min_price=0, stop=0.0),
}


def build_panels(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    p = {c: pd.DataFrame({s: df[c] for s, df in data.items()}).sort_index() for c in ("open", "high", "low", "close", "volume")}
    p["value"] = p["close"] * p["volume"]  # approximation, same as the live discovery filter
    p["is_ks"] = pd.DataFrame({s: s.endswith(".KS") for s in data}, index=p["close"].index)
    return p


def _between(x: pd.DataFrame, lo: float, hi: float) -> pd.DataFrame:
    return (x >= lo) & (x <= hi)


def _zscore(x: pd.DataFrame) -> pd.DataFrame:
    return x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1), axis=0)


# ---------------------------------------------------------------- A: gap-down mean reversion
def mean_reversion(p: dict, tier: dict, cost: float, top_n: int = 5, rng: np.random.Generator | None = None):
    """(selection mask, net return of buying today's open and exiting next open) per stock-day."""
    o, c, v = p["open"], p["close"], p["volume"]
    prev_close = c.shift(1)
    gap = o / prev_close - 1
    mom20 = prev_close / c.shift(21) - 1
    prev_vol_ratio = v.shift(1) / v.rolling(20).mean().shift(1)
    lo, hi = tier["gap"]
    eligible = (
        _between(gap, lo, hi) & (mom20 <= tier["mom_max"]) & (prev_vol_ratio <= 2.0)
        & (p["value"].shift(1) >= tier["prev_tv_min"]) & (prev_close >= tier["min_price"])
    )
    score = (-_zscore(gap) - _zscore(mom20)) if rng is None else pd.DataFrame(rng.random(gap.shape), gap.index, gap.columns)
    selected = score.where(eligible).rank(axis=1, ascending=False, method="first") <= top_n
    exit_px = o.shift(-1)
    if tier["stop"] > 0:  # close-stop: if the close is already >2% under the open, get out at the close
        exit_px = exit_px.where(~(c < o * (1 - tier["stop"])), c)
    return selected, exit_px / o - 1 - cost, eligible


def portfolio(selected: pd.DataFrame, net: pd.DataFrame, start: int, max_weight: float = 0.5) -> dict:
    """Equal weight across the day's picks (capped per position), one-day round trips compounded."""
    taken = selected & net.notna()
    n = taken.sum(axis=1)
    weight = (1 / n.where(n > 0)).clip(upper=max_weight).fillna(0)
    day_ret = (net.where(taken).fillna(0).mul(weight, axis=0)).sum(axis=1).iloc[start:]
    equity = (1 + day_ret).cumprod()
    years = len(day_ret) / 252
    sharpe = day_ret.mean() / day_ret.std() * np.sqrt(252) if day_ret.std() > 0 else float("nan")
    per_trade = net.where(taken).iloc[start:].stack()
    return {
        "return%": (equity.iloc[-1] - 1) * 100, "CAGR%": (equity.iloc[-1] ** (1 / years) - 1) * 100,
        "mdd%": max_drawdown_pct(equity), "sharpe": sharpe, "trades": len(per_trade),
        "avg_net%": per_trade.mean() * 100, "win%": (per_trade > 0).mean() * 100,
    }


# ---------------------------------------------------------------- B: volume inflow then decline
def volume_decline_signals(p: dict, multiplier: float, min_value: float = 1e9) -> dict[str, pd.DataFrame]:
    v = p["volume"]
    prior_mean = v.rolling(20).mean().shift(1)

    def prev(flag):  # yesterday's flag, False where unknown
        return flag.shift(1, fill_value=False).eq(True)

    day1 = (v > multiplier * prior_mean) & (v >= 1000)
    day2 = prev(day1) & (v > v.shift(1))
    dec1 = prev(day2) & (v < v.shift(1))
    dec2 = prev(dec1) & (v < v.shift(1))
    dec3 = prev(dec2) & (v < v.shift(1))
    ok = p["value"] >= min_value
    return {"1 declining day": dec1 & ok, "2 declining days": dec2 & ok, "3 declining days": dec3 & ok}


def next_open_returns(p: dict, cost: float) -> dict[str, pd.DataFrame]:
    """Buy the NEXT open after the signal day; sell that day's close / 3 / 5 days later."""
    o1 = p["open"].shift(-1)
    return {f"D+{n}": p["close"].shift(-n) / o1 - 1 - cost for n in (1, 3, 5)}


# ---------------------------------------------------------------- C: closing-price bet
def closing_bet_masks(p: dict) -> dict[str, pd.DataFrame]:
    o, h, l, c, v = (p[k] for k in ("open", "high", "low", "close", "volume"))
    ret = c / c.shift(1) - 1
    new_high = h >= h.rolling(60).max()
    value_floor = pd.DataFrame(np.where(p["is_ks"], 5e10, 2e10), c.index, c.columns)
    core = (
        new_high & _between(ret, 0.05, 0.25) & (p["value"] >= value_floor)
        & (v >= 3 * v.rolling(60).mean().shift(1)) & (c >= 0.97 * h)
    )
    body = ((c - o) >= 0.7 * (h - l)) & (c > o)
    disparity = _between(c / c.rolling(5).mean(), 1.00, 1.15)
    # market filter: equal-weight proxy of each market must close above its own 5-day mean (no index data cached)
    mkt_ok = pd.DataFrame(False, c.index, c.columns)
    for is_ks in (True, False):
        cols = c.columns[p["is_ks"].iloc[0].to_numpy() == is_ks]
        proxy = (1 + ret[cols].mean(axis=1).fillna(0)).cumprod()
        mkt_ok[cols] = np.repeat((proxy >= proxy.rolling(5).mean()).to_numpy()[:, None], len(cols), axis=1)
    return {
        "5 core conditions": core,
        "core + candle + disparity": core & body & disparity,
        "all (incl. market filter)": core & body & disparity & mkt_ok,
    }


def close_entry_returns(p: dict, cost: float) -> dict[str, pd.DataFrame]:
    c = p["close"]
    return {
        "next open": p["open"].shift(-1) / c - 1 - cost, "next close": c.shift(-1) / c - 1 - cost,
        "3d close": c.shift(-3) / c - 1 - cost, "5d close": c.shift(-5) / c - 1 - cost,
    }


# ---------------------------------------------------------------- reporting
HOLD_DAYS = {"next open": 1, "next close": 1, "D+1": 1, "3d close": 3, "D+3": 3, "5d close": 5, "D+5": 5}


def event_table(label_masks: dict, returns: dict, base_mask: pd.DataFrame, start: int) -> pd.DataFrame:
    ok_rows = np.zeros(base_mask.shape, bool)
    ok_rows[start:] = True
    rows = []
    for label, mask in label_masks.items():
        m0 = mask.to_numpy() & ok_rows
        for horizon, net in returns.items():
            arr = net.to_numpy()
            m = m0 & np.isfinite(arr)
            if m.sum() < 5:
                continue
            diff, t = excess_t(arr * 100, m, base_mask.to_numpy() & ok_rows & np.isfinite(arr), horizon=HOLD_DAYS[horizon], min_n=1)
            rows.append({
                "signal": label, "exit": horizon, "n": int(m.sum()), "mean net%": arr[m].mean() * 100,
                "win%": (arr[m] > 0).mean() * 100, "universe net%": np.nanmean(arr[base_mask.to_numpy() & ok_rows]) * 100,
                "excess%": diff, "t": t,
            })
    return pd.DataFrame(rows)


def main() -> None:
    import yaml

    logging.disable(logging.WARNING)
    pd.set_option("display.width", 230, "display.float_format", lambda x: f"{x:,.2f}")
    cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
    ours = 2 * cfg["costs"]["commission_pct"] / 100 + cfg["costs"]["sell_tax_pct"] / 100  # 0.21%
    data = load_data(cfg["backtest"]["history_days"])
    p = build_panels(data)
    start = WARMUP_BARS
    liquid = (p["close"] >= cfg["discovery"]["min_price"]) & (p["value"] >= cfg["discovery"]["min_trading_value"])
    print(f"universe={len(data)} (today's KOSPI200+KOSDAQ150)  {p['close'].index[start].date()} -> {p['close'].index[-1].date()}  "
          f"our round-trip cost {ours * 100:.2f}%  (repo A assumes 0.35%, B 0.25%)")

    print("")
    print("== A. GAP-DOWN OVERNIGHT MEAN REVERSION (buy open, sell next open) - repo claims on ~2,300 tickers, 2022-02..2026-04: "
          "CAGR +101.8%, Sharpe 2.53, MDD 18.7%, win 59.5% ==")
    rows = []
    for name, tier in TIERS.items():
        for cost_name, cost in (("0.21% (ours)", ours), ("0.35% (repo)", 0.0035)):
            sel, net, _ = mean_reversion(p, tier, cost)
            rows.append({"tier": name, "cost": cost_name, **portfolio(sel, net, start)})
        # control: same eligibility, random 5 instead of the -z(gap)-z(mom) ranking
        sel_r, net_r, _ = mean_reversion(p, tier, ours, rng=np.random.default_rng(0))
        rows.append({"tier": name + "  [RANDOM among eligible]", "cost": "0.21% (ours)", **portfolio(sel_r, net_r, start)})
    print(pd.DataFrame(rows).to_string(index=False))

    # per-trade edge vs the whole liquid universe (same buy-open / sell-next-open), paired by day
    ok_rows = np.zeros(liquid.shape, bool)
    ok_rows[start:] = True
    base_net = (p["open"].shift(-1) / p["open"] - 1 - ours).to_numpy()
    base = liquid.to_numpy() & ok_rows & np.isfinite(base_net)
    print(f"-- A per trade vs universe (universe mean {np.nanmean(base_net[base]) * 100:+.3f}% per open->next-open, net of cost) --")
    edge_rows = []
    for name, tier in TIERS.items():
        sel, net, elig = mean_reversion(p, tier, ours)
        arr = net.to_numpy()
        for label, mask in (("top-5 ranked", sel), ("ALL eligible", elig)):
            m = mask.to_numpy() & ok_rows & np.isfinite(arr)
            diff, t = excess_t(arr * 100, m, base, horizon=1, min_n=1)
            edge_rows.append({"tier": name, "picks": label, "n": int(m.sum()), "mean net%": arr[m].mean() * 100,
                              "win%": (arr[m] > 0).mean() * 100, "excess%": diff, "t": t})
    print(pd.DataFrame(edge_rows).to_string(index=False))

    print("")
    print("== B. VOLUME INFLOW THEN DECLINE (buy NEXT open; repo claims +1.20% net at D+1, win 52%, KOSDAQ all, cost 0.25%) ==")
    returns_b = next_open_returns(p, 0.0025)
    masks_b = {f"x{m} / {k}": s for m in (1.5, 2.0, 3.0) for k, s in volume_decline_signals(p, m).items()}
    print(event_table(masks_b, returns_b, liquid, start).to_string(index=False))

    print("")
    print("== C. CLOSING-PRICE BET, 5 conditions (buy the close; exit rule undocumented -> several horizons; our cost) ==")
    print(event_table(closing_bet_masks(p), close_entry_returns(p, ours), liquid, start).to_string(index=False))


if __name__ == "__main__":
    main()
