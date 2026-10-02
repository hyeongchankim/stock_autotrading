"""Backtest: should the discovery sleeve's closing-price entries be sold at the
NEXT OPEN instead of being held as a swing trade?

Run: python -m backtest.overnight  (uses the price cache from backtest.discovery_overlay - no KRX access)

Why: overnight (close->open) returns beat intraday (open->close) in much of the
literature, and Korean studies report an opening reversal (the open is strong,
then fades) - so selling at the next open might keep the gap and skip the fade.

Entries are the live discovery sleeve's: top-N 등락률 movers (price/value/limit
filters) that ALSO pass the engine's own gates - close_strength BUY, volume
surge (VolumeFilter) and regime ADX >= trend threshold. Only the exit differs.
Costs: commission both ways + sell tax from config (~0.21% round trip).
Same blind spots as the other backtests: today's index constituents
(survivorship), fills at the exact close/open, no intraday checkpoint rule.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from backtest.discovery_overlay import ROOT, WARMUP_BARS, candidate_mask, load_data
from backtest.early_setup import build_panels, excess_t
from backtest.metrics import max_drawdown_pct
from utils.indicators import adx

HORIZONS = {"next open": None, "next close": 1, "3d close": 3, "5d close": 5}


def trade_returns(panels: dict, round_trip_cost: float) -> dict[str, pd.DataFrame]:
    """Net return (fraction) of buying today's close and selling at each horizon."""
    close, open_ = panels["close"], panels["open"]
    gross = {
        "next open": open_.shift(-1) / close - 1,
        "next close": close.shift(-1) / close - 1,
        "3d close": close.shift(-3) / close - 1,
        "5d close": close.shift(-5) / close - 1,
    }
    out = {name: g - round_trip_cost for name, g in gross.items()}
    out["_intraday_next"] = close.shift(-1) / open_.shift(-1) - 1  # the part a next-open exit avoids
    return out


def portfolio(entries: pd.DataFrame, net: pd.DataFrame, weight: float, start: int) -> dict:
    """Equal-weight `weight` per entry, one-day (or fixed-horizon) round trips
    compounded day by day. Fixed horizons > 1 day are NOT stacked (capital would
    overlap) - use the overnight/next-close rows for the portfolio view.
    """
    taken = entries & net.notna()
    day_ret = (net.where(taken).fillna(0) * weight).sum(axis=1).iloc[start:]
    equity = (1 + day_ret).cumprod()
    trades = taken.iloc[start:].to_numpy().sum()
    per_trade = net.where(taken).iloc[start:].stack()
    return {
        "return%": (equity.iloc[-1] - 1) * 100,
        "mdd%": max_drawdown_pct(equity),
        "trades": int(trades),
        "avg_net%": float(per_trade.mean() * 100),
        "win%": float((per_trade > 0).mean() * 100),
        "_equity": equity,
    }


def group_table(panels: dict, nets: dict, groups: dict[str, pd.DataFrame], base: pd.DataFrame, start: int) -> pd.DataFrame:
    ok = np.zeros(panels["close"].shape, bool)
    ok[start:] = True
    rows = []
    for label, mask in groups.items():
        m = mask.to_numpy() & ok & nets["next open"].notna().to_numpy()
        if m.sum() == 0:
            continue
        row = {"group": label, "n": int(m.sum())}
        for name in HORIZONS:
            row[f"{name}%"] = float(np.nanmean(nets[name].to_numpy()[m]) * 100)
        row["win(open)%"] = float((nets["next open"].to_numpy()[m] > 0).mean() * 100)
        row["intraday-after%"] = float(np.nanmean(nets["_intraday_next"].to_numpy()[m]) * 100)
        # overnight edge vs every liquid stock, paired by day (1-day returns don't overlap -> horizon=1)
        diff, t = excess_t(nets["next open"].to_numpy() * 100, m, base.to_numpy() & ok, horizon=1, min_n=1)
        row["vs universe"], row["t"] = diff, t
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    import yaml

    logging.disable(logging.WARNING)
    pd.set_option("display.width", 220, "display.float_format", lambda x: f"{x:,.2f}")
    cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
    disc, costs, regime = cfg["discovery"], cfg["costs"], cfg["regime_filter"]
    round_trip = 2 * costs["commission_pct"] / 100 + costs["sell_tax_pct"] / 100

    data = load_data(cfg["backtest"]["history_days"])
    panels = build_panels(data, recent=3)
    panels["open"] = pd.DataFrame({s: df["open"] for s, df in data.items()}).sort_index().reindex(panels["close"].index)
    adx_panel = pd.DataFrame({s: adx(df, regime["adx_period"]) for s, df in data.items()}).reindex(panels["close"].index)
    nets = trade_returns(panels, round_trip)

    n = disc["max_candidates"]
    eligible = (
        (panels["close"] >= disc["min_price"]) & (panels["chg"].abs() <= disc["max_change_pct"])
        & (panels["close"] * panels["volume"] >= disc["min_trading_value"])
    )
    top5 = candidate_mask(panels, disc, None, "-")
    gates = panels["close_strength"] & panels["volume_surge"] & (adx_panel >= regime["trend_threshold"])
    rand_rank = pd.DataFrame(np.random.default_rng(0).random(eligible.shape), eligible.index, eligible.columns)
    random5 = rand_rank.where(eligible).rank(axis=1, ascending=False, method="first") <= n

    start = WARMUP_BARS
    print(f"universe={len(data)}  {panels['close'].index[start].date()} -> {panels['close'].index[-1].date()}  "
          f"round-trip cost {round_trip * 100:.3f}% (already subtracted from every return below)")
    groups = {
        "all liquid stock-days (universe)": eligible,
        "top-5 movers (candidates)": top5,
        "top-5 + live gates  <- live entries": top5 & gates,
        "any eligible + gates (not just top-5)": eligible & gates,
        "random 5 eligible": random5,
        "random 5 + gates": random5 & gates,
    }
    print("")
    print("== PER-TRADE: buy the close, sell at each horizon (net of costs, mean %) ==")
    print(group_table(panels, nets, groups, eligible, start).to_string(index=False))

    print("")
    print("== PORTFOLIO (20% per entry, max 5/day, 1-day round trips compounded) ==")
    rows = []
    for label, entries in (("live entries", top5 & gates), ("top-5 movers", top5), ("random 5 + gates", random5 & gates)):
        for exit_name in ("next open", "next close"):
            res = portfolio(entries, nets[exit_name], disc["position_size_pct"], start)
            rows.append({"entries": label, "exit": exit_name, **{k: v for k, v in res.items() if not k.startswith("_")}})
    print(pd.DataFrame(rows).to_string(index=False))

    print("")
    print("== BY YEAR: live entries sold at next open vs next close (portfolio return %) ==")
    eqs = {name: portfolio(top5 & gates, nets[name], disc["position_size_pct"], start)["_equity"] for name in ("next open", "next close")}
    yearly = pd.DataFrame({
        name: eq.groupby(eq.index.year).apply(lambda g: (g.iloc[-1] / g.iloc[0] - 1) * 100) for name, eq in eqs.items()
    })
    print(yearly.to_string())


if __name__ == "__main__":
    main()
