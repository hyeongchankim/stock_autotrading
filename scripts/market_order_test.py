"""Does the KIS DEMO account actually fill a MARKET sell (ORD_DVSN 01)? Buys 1 share, sells it at market, reports.

Why: config broker.kis.exit_order_type can send exits as market orders so a stop-loss can't sit unfilled while
the price falls (2026-10-13), but whether the demo environment fills ORD_DVSN=01 was never verified, and this
project has been burned before by trusting KIS docs/samples over a real call.

    python scripts/market_order_test.py            # dry run: picks the stock and prints the plan, places NOTHING
    python scripts/market_order_test.py --place    # places the two 1-share orders (demo money only)

Safety: refuses to run unless broker.kis.env is "demo" (and the session really is the demo one), only during market
hours on a trading day, exactly 1 share, a stock no sleeve holds and that is not among today's top candidates (so
no sleeve's own fill detection can be confused by it). Run it between 15-minute cycles (e.g. 10:05) so it does not
overlap a sleeve cycle. If the sell fails the 1 share is left in the account and the symbol is printed.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from broker.kis_broker import KisBroker  # noqa: E402
from data.kis_data_feed import KisDataFeed  # noqa: E402
from data.market_calendar import market_closed_today  # noqa: E402
from screening.discover import get_universe  # noqa: E402
from screening.universe_quotes import fetch_quotes, universe_candidates  # noqa: E402
from strategies.base import Signal  # noqa: E402

MARKET_OPEN, LAST_ENTRY = dtime(9, 10), dtime(15, 15)
STATE_FILES = ("state.json", "macd_state.json", "discovery_state.json")


def pick_test_symbol(
    quotes: pd.DataFrame, held_tickers: set[str], busy_tickers: set[str],
    min_price: float = 5_000, max_price: float = 30_000, min_value: float = 1e10,
) -> str | None:
    """Cheapest liquid stock that no sleeve holds and that isn't in `busy_tickers` (today's top candidates).
    Returns the bare ticker, or None."""
    ok = quotes[
        quotes["price"].between(min_price, max_price)
        & (quotes["trading_value"] >= min_value)
        & ~quotes["ticker"].isin(held_tickers | busy_tickers)
    ].sort_values("price")
    return None if ok.empty else str(ok.iloc[0]["ticker"])


def held_by_sleeves() -> set[str]:
    held: set[str] = set()
    for name in STATE_FILES:
        path = ROOT / name
        if path.exists():
            ledger = json.loads(path.read_text(encoding="utf-8")).get("kis_cash_ledger", {})
            held |= {s.split(".")[0] for s in ledger.get("positions", {})}
            held |= {o["symbol"].split(".")[0] for o in ledger.get("pending_orders", [])}
    return held


def main() -> int:
    place = "--place" in sys.argv
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    env = cfg["broker"]["kis"]["env"]
    if env != "demo":
        print(f"REFUSING: broker.kis.env is {env!r} - this test only runs against the demo account.")
        return 2

    buyer = KisBroker(env="demo")                            # unscoped: account-inspection mode, no sleeve ledger
    seller = KisBroker(env="demo", exit_order_type="market")
    if buyer.session.env != "demo" or "openapivts" not in buyer.session.base_url:
        print("REFUSING: the KIS session is not the demo (모의투자) one.")
        return 2

    now = datetime.now()
    if not (MARKET_OPEN <= now.time() <= LAST_ENTRY) or now.weekday() >= 5 or market_closed_today(KisDataFeed(env="demo")):
        print(f"REFUSING: {now:%Y-%m-%d %H:%M} is outside market hours / not a trading day ({MARKET_OPEN:%H:%M}-{LAST_ENTRY:%H:%M}).")
        return 2

    universe = get_universe(max_age_days=cfg["discovery"].get("universe_cache_days", 7))
    quotes = fetch_quotes(buyer.session, [s.split(".")[0] for s in universe] + [])
    busy = set(universe_candidates(buyer.session, universe, cfg["discovery"]).head(30)["ticker"])
    ticker = pick_test_symbol(quotes, held_by_sleeves() | {s.split(".")[0] for s in buyer._account_positions()}, busy)
    if ticker is None:
        print("no suitable test stock found (cheap, liquid, not held, not a top candidate)")
        return 1
    row = quotes[quotes["ticker"] == ticker].iloc[0]
    suffix = next(s.split(".")[1] for s in universe if s.startswith(ticker))
    symbol, quote = f"{ticker}.{suffix}", float(row["price"])
    print(f"test stock: {symbol} {row['name']}  quote {quote:,.0f}  trading value {row['trading_value'] / 1e8:,.0f}억")
    print("plan: BUY 1 share (limit 1% above the quote so it fills), then SELL 1 share as a MARKET order, then compare.")
    if not place:
        print("dry run - nothing placed. Re-run with --place to place the two 1-share demo orders.")
        return 0

    t0 = time.time()
    bought = buyer.place_order(symbol, Signal.BUY, 1, quote * 1.01)
    print(f"BUY : filled={bought.filled} qty={bought.quantity} price={bought.price:,.0f}  ({bought.message})  [{time.time() - t0:.1f}s]")
    if not bought.filled:
        print("buy did not fill - nothing to sell. (If an unfilled buy order is still open it will expire at the close.)")
        return 1

    sell_quote = float(fetch_quotes(buyer.session, [ticker]).iloc[0]["price"])
    t1 = time.time()
    sold = seller.place_order(symbol, Signal.SELL, 1, sell_quote)
    print(f"SELL: filled={sold.filled} qty={sold.quantity} avg price={sold.price:,.0f} vs quote {sell_quote:,.0f} "
          f"(slippage {(sold.price / sell_quote - 1) * 100:+.2f}%)  ({sold.message})  [{time.time() - t1:.1f}s]")
    left = {s.split(".")[0]: p.quantity for s, p in buyer._account_positions().items()}.get(ticker, 0)
    print(f"account still holds {left} share(s) of {ticker}")
    if sold.filled and left == 0:
        print("RESULT: the demo account fills market sells -> safe to set broker.kis.exit_order_type: \"market\".")
        return 0
    print(f"RESULT: market sell NOT confirmed. {left} share of {symbol} may remain in the account - check it by hand.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
