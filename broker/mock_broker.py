"""Paper-trading broker: fills every valid order immediately at the given price.

Swap this for a real broker adapter once an API key is available - the engine
only ever depends on BrokerBase, so no other code needs to change.
"""
from __future__ import annotations

from broker.base import BrokerBase, OrderResult
from portfolio.portfolio import Portfolio
from strategies.base import Signal


class MockBroker(BrokerBase):
    def __init__(self, seed_capital: float, commission_pct: float = 0.0, sell_tax_pct: float = 0.0):
        """commission_pct is charged on both buy and sell; sell_tax_pct
        (e.g. Korean securities transaction tax) only on sell. Both are
        percentages (0.015 means 0.015%), applied to the fill price so the
        backtest reflects real trading costs instead of a frictionless ideal.
        """
        self.portfolio = Portfolio(seed_capital)
        self.commission_pct = commission_pct
        self.sell_tax_pct = sell_tax_pct
        self._prices: dict[str, float] = {}
        self.order_log: list[OrderResult] = []

    def set_price_lookup(self, prices: dict[str, float]) -> None:
        """Called once per trading cycle with the latest known close price per
        symbol. Prices are merged (not replaced) so a symbol missing from this
        cycle keeps its last known price instead of erroring out.
        """
        self._prices.update(prices)

    def get_cash_balance(self) -> float:
        return self.portfolio.cash

    def get_positions(self) -> dict:
        return self.portfolio.positions

    def get_current_price(self, symbol: str) -> float:
        if symbol not in self._prices:
            raise RuntimeError(f"No known price for {symbol}; call set_price_lookup first")
        return self._prices[symbol]

    def place_order(
        self, symbol: str, side: Signal, quantity: int, price: float | None = None
    ) -> OrderResult:
        if quantity <= 0:
            result = OrderResult(symbol, side, quantity, price or 0.0, False, "quantity must be positive")
            self.order_log.append(result)
            return result

        fill_price = price if price is not None else self.get_current_price(symbol)

        if side == Signal.BUY:
            effective_price = fill_price * (1 + self.commission_pct / 100)
            cost = quantity * effective_price
            if cost > self.portfolio.cash:
                result = OrderResult(symbol, side, quantity, fill_price, False, "insufficient cash")
            else:
                self.portfolio.apply_buy(symbol, quantity, effective_price)
                result = OrderResult(symbol, side, quantity, fill_price, True, "filled")

        elif side == Signal.SELL:
            position = self.portfolio.positions.get(symbol)
            if position is None or position.quantity < quantity:
                result = OrderResult(symbol, side, quantity, fill_price, False, "insufficient position")
            else:
                effective_price = fill_price * (1 - self.commission_pct / 100 - self.sell_tax_pct / 100)
                realized_pnl = self.portfolio.apply_sell(symbol, quantity, effective_price)
                result = OrderResult(symbol, side, quantity, fill_price, True, "filled", realized_pnl)

        else:
            result = OrderResult(symbol, side, quantity, fill_price, False, "HOLD is not an order side")

        self.order_log.append(result)
        return result
