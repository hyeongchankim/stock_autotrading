"""Local shadow tracking for the hybrid mode's Buy & Hold sleeve in paper/live
trading - mirrors backtest.benchmark.run_buy_and_hold's equal-weight,
buy-once-and-hold-forever logic, but as a purchase-once/mark-to-market-after
object suitable for persisting across separate process runs (see
utils/state_store.py).

Deliberately never places a real broker order: a real KIS account is a
single pot of cash and positions, so there's no way to carve out a second
sleeve's capital from it without either restricting it to symbols the
strategy sleeve never touches, or risking the two sleeves' orders on the
same symbol blending together (shared avg_price, blocked re-entry - see
engine/trading_engine.py's "one open position per symbol" assumption). The
backtest benchmark has the same property: it's priced as if bh_seed had
been deployed, without ever being real capital at risk through a broker.
"""
from __future__ import annotations

from datetime import date


class BuyAndHoldSleeve:
    def __init__(self, seed_capital: float, watchlist: list[str]):
        self.seed_capital = seed_capital
        self.watchlist = watchlist
        self.purchased = False
        self.purchase_date: str | None = None
        self.shares: dict[str, int] = {}
        self.cash = seed_capital

    def ensure_purchased(self, prices: dict[str, float]) -> None:
        """Equal-weights seed_capital across every watchlist symbol with a
        known price, once. No-ops if already purchased. If some symbols are
        missing a price this call, they're simply skipped (their allocation
        sits as leftover cash) rather than delaying the whole purchase -
        same one-shot-entry behavior as the backtest benchmark.
        """
        if self.purchased or not self.watchlist:
            return

        known = {s: p for s, p in prices.items() if s in self.watchlist and p > 0}
        if not known:
            return  # no prices available yet - try again next run

        allocation_per_symbol = self.seed_capital / len(self.watchlist)
        cash = self.seed_capital
        shares: dict[str, int] = {}
        for symbol, price in known.items():
            qty = int(allocation_per_symbol // price)
            if qty <= 0:
                continue
            shares[symbol] = qty
            cash -= qty * price

        self.shares = shares
        self.cash = cash
        self.purchased = True
        self.purchase_date = date.today().isoformat()

    def current_value(self, prices: dict[str, float]) -> float:
        holdings_value = sum(
            qty * prices[symbol] for symbol, qty in self.shares.items() if symbol in prices
        )
        return self.cash + holdings_value

    def to_dict(self) -> dict:
        return {
            "purchased": self.purchased,
            "purchase_date": self.purchase_date,
            "shares": self.shares,
            "cash": self.cash,
            "seed_capital": self.seed_capital,
        }

    def restore(self, state: dict) -> None:
        if not state:
            return
        self.purchased = state.get("purchased", False)
        self.purchase_date = state.get("purchase_date")
        self.shares = state.get("shares", {})
        self.cash = state.get("cash", self.seed_capital)
