"""Backtest of a proposed 발굴형 rule (2026-10-08): volume surge + gap-up out of a basing pattern.

User's description, and how each part is coded (all knobs are parameters):
- "최근 일주일 평균 거래량보다 두 배 이상 들어오면서"  -> volume_t >= 2.0 x mean(volume of the previous 5 days)
- "추세선 위에서 갭상승 시작"                           -> open_t above the trend line (SMA `trend_sma`, as of yesterday) AND a real
                                                           gap-up: open_t > yesterday's high and open_t/prev_close-1 >= gap_min
- "상승 후 횡보, 하락 후 횡보"                          -> four consecutive blocks of L days before today:
                                                           up (>= +up) -> flat (range <= flat1) -> down (<= -down) -> flat (range <= flat2)
- "횡보 시작 시점의 최고가 위로 갭상승"                  -> open_t > highest high of the last (flat2) block ("box high")
Layers are cumulative so each added condition's contribution is visible:
  L1 volume + trend-line gap-up | L2 + breaks above the last flat box | L2b + that box formed after a decline |
  L3 + the full up/flat/down/flat history (4 blocks).

The signal needs the day's full volume, so it is known at the CLOSE: evaluated for a close buy (what the 15:20 discovery
run does - same close look-ahead caveat as the other backtests) and for a next-open buy.
Universe: today's 347 KOSPI200+KOSDAQ150 names (survivorship); cost: this repo's round trip. Run:
python -m backtest.gap_breakout
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from backtest.discovery_overlay import ROOT, WARMUP_BARS, load_data
from backtest.github_strategies import HOLD_DAYS, build_panels, event_table
from utils.indicators import sma

HOLD_DAYS.update({"10d close": 10, "D+10": 10})


@dataclass(frozen=True)
class Rule:
    volume_mult: float = 2.0
    volume_days: int = 5
    trend_sma: int = 60
    gap_min: float = 0.01      # open vs previous close
    block: int = 10            # L: length of each of the four blocks, in trading days
    flat2: float = 0.08        # max (high-low)/mean-close of the last, "current" box
    flat1: float = 0.10        # ... of the earlier box
    down: float = 0.10         # decline between the boxes
    up: float = 0.10           # rise before the first box


def _range(p: dict, length: int, lag: int) -> pd.DataFrame:
    """(max high - min low) / mean close of the `length`-day block that ended `lag` days before today."""
    hi = p["high"].rolling(length).max().shift(lag)
    lo = p["low"].rolling(length).min().shift(lag)
    return (hi - lo) / p["close"].rolling(length).mean().shift(lag)


def layers(p: dict, r: Rule) -> dict[str, pd.DataFrame]:
    o, h, c, v = p["open"], p["high"], p["close"], p["volume"]
    n = r.block
    box_high = h.rolling(n).max().shift(1)            # highest high of the last flat block (ends yesterday)
    volume_ok = v >= r.volume_mult * v.shift(1).rolling(r.volume_days).mean()
    gap_up = (o > h.shift(1)) & (o / c.shift(1) - 1 >= r.gap_min)
    above_trend = o > sma(c, r.trend_sma).shift(1)
    flat_box = _range(p, n, 1) <= r.flat2
    broke_out = o > box_high
    declined = (c.shift(n + 1) / c.shift(2 * n) - 1) <= -r.down          # block ending at t-n-1
    earlier_flat = _range(p, n, 2 * n + 1) <= r.flat1                       # block ending at t-2n-1
    rose = (c.shift(3 * n + 1) / c.shift(4 * n) - 1) >= r.up                # block ending at t-3n-1
    # the box started `down` or more below the highest close of the 3 blocks before it (a decline INTO the box;
    # the earlier up/flat phases are not required - the strict 4-block history almost never occurs)
    peak = c.rolling(3 * n).max().shift(n + 1)
    decline_into_box = (c.shift(n) / peak - 1) <= -r.down
    l1 = volume_ok & gap_up & above_trend
    l2 = l1 & flat_box & broke_out
    l2b = l2 & decline_into_box
    l3 = l2b & declined & earlier_flat & rose
    return {
        "L1 volume x2 + trend gap-up": l1, "L2 + breaks the flat box": l2,
        "L2b + box formed after a decline": l2b, "L3 + up/flat/down/flat history": l3,
    }


def close_horizons(p: dict, cost: float) -> dict[str, pd.DataFrame]:
    c = p["close"]
    return {
        "next open": p["open"].shift(-1) / c - 1 - cost, "next close": c.shift(-1) / c - 1 - cost,
        "3d close": c.shift(-3) / c - 1 - cost, "5d close": c.shift(-5) / c - 1 - cost, "10d close": c.shift(-10) / c - 1 - cost,
    }


def open_horizons(p: dict, cost: float) -> dict[str, pd.DataFrame]:
    """Buy the NEXT open after the signal; sell that day's / 3 / 5 / 10 days later at the close."""
    o1 = p["open"].shift(-1)
    return {f"D+{k}": p["close"].shift(-k) / o1 - 1 - cost for k in (1, 3, 5, 10)}


def yearly_counts(mask: pd.DataFrame, start: int) -> pd.Series:
    m = mask.copy()
    m.iloc[:start] = False
    return m.sum(axis=1).groupby(m.index.year).sum()


def main() -> None:
    import yaml

    logging.disable(logging.WARNING)
    pd.set_option("display.width", 230, "display.float_format", lambda x: f"{x:,.2f}", "display.max_rows", 200)
    cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
    cost = 2 * cfg["costs"]["commission_pct"] / 100 + cfg["costs"]["sell_tax_pct"] / 100
    data = load_data(cfg["backtest"]["history_days"])
    p = build_panels(data)
    start = WARMUP_BARS
    liquid = (p["close"] >= cfg["discovery"]["min_price"]) & (p["value"] >= cfg["discovery"]["min_trading_value"])
    base = Rule()
    print(f"universe={len(data)}  {p['close'].index[start].date()} -> {p['close'].index[-1].date()}  cost {cost * 100:.2f}% round trip")
    print(f"rule: volume>={base.volume_mult}x prior {base.volume_days}d mean | open>{base.trend_sma}d MA & gap-up>={base.gap_min:.0%} over prev high/close | "
          f"4 blocks of {base.block}d: up>={base.up:.0%}, flat<={base.flat1:.0%}, down>={base.down:.0%}, flat<={base.flat2:.0%} | open > box high")

    masks = {k: (m & liquid) for k, m in layers(p, base).items()}  # same liquidity floor as the live 1차 필터
    print("")
    print("== SIGNAL COUNTS by year (liquid names only) ==")
    print(pd.DataFrame({k: yearly_counts(m, start) for k, m in masks.items()}).fillna(0).astype(int).T.to_string())

    print("")
    print("== ENTRY = CLOSE of the signal day ==")
    print(event_table(masks, close_horizons(p, cost), liquid, start).to_string(index=False))
    print("")
    print("== ENTRY = NEXT OPEN after the signal ==")
    print(event_table(masks, open_horizons(p, cost), liquid, start).to_string(index=False))

    print("")
    print("== MOST RECENT L3 / L2 signals (sanity check) ==")
    for name in ("L3 + up/flat/down/flat history", "L2 + breaks the flat box"):
        m = masks[name].copy()
        m.iloc[:start] = False
        hits = m.stack()
        hits = hits[hits].index.tolist()[-8:]
        print(name, "->", [(d.strftime("%Y-%m-%d"), s) for d, s in hits] or "none")

    print("")
    print("== SENSITIVITY (L2 and L3 counts / mean next-open & 3d return, close entry) - shown for transparency, NOT for picking the best ==")
    rows = []
    cl = close_horizons(p, cost)
    for label, rule in (
        ("default", base), ("block 5d", replace(base, block=5)), ("block 7d", replace(base, block=7)),
        ("trend SMA20", replace(base, trend_sma=20)), ("trend SMA120", replace(base, trend_sma=120)),
        ("down/up 7%", replace(base, down=0.07, up=0.07)), ("down/up 15%", replace(base, down=0.15, up=0.15)),
        ("gap >= 0", replace(base, gap_min=0.0)), ("gap >= 2%", replace(base, gap_min=0.02)),
        ("volume x1.5", replace(base, volume_mult=1.5)), ("volume x3", replace(base, volume_mult=3.0)),
    ):
        for layer, m in layers(p, rule).items():
            if not layer.startswith(("L2", "L3")):  # L2, L2b, L3
                continue
            mm = (m & liquid).to_numpy().copy()
            mm[:start] = False
            row = {"variant": label, "layer": layer.split(" ")[0], "n": int(mm.sum())}
            for h in ("next open", "3d close"):
                arr = cl[h].to_numpy()
                ok = mm & np.isfinite(arr)
                row[h + " %"] = arr[ok].mean() * 100 if ok.sum() >= 5 else float("nan")
            rows.append(row)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
