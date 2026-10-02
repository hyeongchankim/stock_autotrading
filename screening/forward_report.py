"""Summarises screening/forward_log.py: what did the 15:20 scan's candidates
actually do overnight? Run: python -m screening.forward_report (reads
forward_log.json only - no network, no KRX/KIS calls).

Columns (all % and net of the config's round-trip cost where marked):
  scan->close     15:20 price to the day's final close  - the look-ahead the daily-bar backtest ignores
  scan->open+1    15:20 price to the next open, NET      - the real overnight trade (compare backtest +0.11..0.13)
  open+1->close+1 next open to next close                 - the intraday fade after the gap
  scan->close+1   15:20 price held through the next close, NET
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from utils.state_store import StateStore

ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "forward_log.json"


def to_frame(records: list[dict], round_trip_cost: float) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if df.empty:
        return df
    for col in ("close", "open_next", "close_next"):
        if col not in df:
            df[col] = float("nan")
    df["scan->close"] = (df["close"] / df["price_scan"] - 1) * 100
    df["scan->open+1"] = ((df["open_next"] / df["price_scan"] - 1) - round_trip_cost) * 100
    df["open+1->close+1"] = (df["close_next"] / df["open_next"] - 1) * 100
    df["scan->close+1"] = ((df["close_next"] / df["price_scan"] - 1) - round_trip_cost) * 100
    return df


def summarize(df: pd.DataFrame, top_ns: tuple[int, ...] = (3, 5, 10)) -> pd.DataFrame:
    """Mean/win-rate per cut-off (top-N by change%) and for the persistence-qualified subset."""
    if df.empty:
        return df
    groups = {f"top-{n} by change%": df["rank"] <= n for n in top_ns}
    groups["qualified (2+ checkpoints)"] = df["qualified"]
    groups["all logged"] = df["rank"] > 0
    rows = []
    for label, mask in groups.items():
        sub = df[mask & df["open_next"].notna()]
        if sub.empty:
            continue
        rows.append({
            "group": label, "n": len(sub), "days": sub["date"].nunique(),
            "scan->close": sub["scan->close"].mean(), "scan->open+1": sub["scan->open+1"].mean(),
            "win(open)%": (sub["scan->open+1"] > 0).mean() * 100,
            "open+1->close+1": sub["open+1->close+1"].mean(), "scan->close+1": sub["scan->close+1"].mean(),
        })
    return pd.DataFrame(rows)


def main() -> None:
    import yaml

    cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
    costs = cfg["costs"]
    round_trip = 2 * costs["commission_pct"] / 100 + costs["sell_tax_pct"] / 100
    records = StateStore(LOG_FILE).load().get("records", [])
    df = to_frame(records, round_trip)
    pd.set_option("display.width", 200, "display.float_format", lambda x: f"{x:,.2f}")
    if df.empty:
        print("forward_log.json has no records yet (the 15:20 discovery Final run creates them).")
        return
    done = df["open_next"].notna().sum()
    print(f"{len(df)} candidate rows over {df['date'].nunique()} days; {done} have the next-day open "
          f"({df['close_next'].notna().sum()} the next close). round-trip cost {round_trip * 100:.3f}% subtracted where marked NET")
    print(summarize(df).to_string(index=False) if done else "nothing to summarise until a next-day open is filled in")
    print("")
    print("Backtest reference (daily bars, close->open, net): top-5 movers +0.13, live entries +0.11; "
          "next-open -> close -0.20..-0.31")


if __name__ == "__main__":
    main()
