"""Broker adapter contract.

Implement this against a real brokerage API (e.g. KIS Developers, Kiwoom
Open API+) when that integration is ready. Until then, MockBroker is the
paper-trading reference implementation the whole engine is built against.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from strategies.base import Signal


@dataclass
class OrderResult:
    symbol: str
    side: Signal
    quantity: int
    price: float
    filled: bool
    message: str = ""
    realized_pnl: float = 0.0


class BrokerBase(ABC):
    @abstractmethod
    def get_cash_balance(self) -> float: ...

    @abstractmethod
    def get_positions(self) -> dict: ...

    @abstractmethod
    def get_current_price(self, symbol: str) -> float: ...

    @abstractmethod
    def place_order(
        self, symbol: str, side: Signal, quantity: int, price: float | None = None
    ) -> OrderResult: ...

    def get_total_equity(self) -> float:
        cash = self.get_cash_balance()
        positions = self.get_positions()
        holdings_value = sum(
            pos.quantity * self.get_current_price(symbol) for symbol, pos in positions.items()
        )
        return cash + holdings_value
