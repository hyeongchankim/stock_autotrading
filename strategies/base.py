"""Shared types for all signal-generating strategies."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import pandas as pd


class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class StrategyCategory(Enum):
    """Which market regime a strategy is designed for.

    Used by RegimeFilter to decide which strategies are allowed to open new
    positions right now, so a trend strategy and a mean-reversion strategy
    don't fight each other on the same symbol at the same time.
    """

    TREND_FOLLOWING = "trend_following"
    MEAN_REVERSION = "mean_reversion"


@dataclass
class StrategySignal:
    symbol: str
    signal: Signal
    strategy_name: str
    reason: str
    price: float | None = None


class Strategy(ABC):
    """Base class every strategy implements.

    A strategy only decides BUY / SELL / HOLD from price history; it never
    touches position sizing, stop-loss/take-profit, or order execution -
    those are RiskManager's and the broker's job.
    """

    name: str = "base_strategy"
    category: StrategyCategory = StrategyCategory.TREND_FOLLOWING

    @abstractmethod
    def min_bars_required(self) -> int:
        """Minimum number of OHLCV bars needed before a signal can be generated."""

    @abstractmethod
    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        """ohlcv must have columns open, high, low, close, volume, sorted ascending by date."""

    def _hold(self, symbol: str, reason: str) -> StrategySignal:
        return StrategySignal(symbol=symbol, signal=Signal.HOLD, strategy_name=self.name, reason=reason)
