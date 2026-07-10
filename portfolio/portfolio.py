"""In-memory cash/position bookkeeping used by MockBroker for paper trading."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Position:
    symbol: str
    quantity: int
    avg_price: float


class Portfolio:
    def __init__(self, seed_capital: float):
        self.seed_capital = seed_capital
        self.cash = seed_capital
        self.positions: dict[str, Position] = {}

    def total_equity(self, price_lookup: Callable[[str], float]) -> float:
        holdings_value = sum(
            pos.quantity * price_lookup(symbol) for symbol, pos in self.positions.items()
        )
        return self.cash + holdings_value

    def apply_buy(self, symbol: str, quantity: int, price: float) -> None:
        cost = quantity * price
        self.cash -= cost
        existing = self.positions.get(symbol)
        if existing is None:
            self.positions[symbol] = Position(symbol, quantity, price)
        else:
            new_quantity = existing.quantity + quantity
            new_avg_price = (existing.avg_price * existing.quantity + cost) / new_quantity
            existing.quantity = new_quantity
            existing.avg_price = new_avg_price

    def apply_sell(self, symbol: str, quantity: int, price: float) -> float:
        """Returns realized P&L for this sell."""
        position = self.positions[symbol]
        realized_pnl = (price - position.avg_price) * quantity
        self.cash += quantity * price
        position.quantity -= quantity
        if position.quantity <= 0:
            del self.positions[symbol]
        return realized_pnl
