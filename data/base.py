"""Market data source contract.

Real integration should eventually pull OHLCV from the brokerage's own
quote/history endpoint. YFinanceDataFeed is a temporary stand-in so
strategies and the engine can be built and tested before that exists.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class DataFeedBase(ABC):
    @abstractmethod
    def get_ohlcv(self, symbol: str, interval: str = "1d", lookback: int = 100) -> pd.DataFrame:
        """Returns a DataFrame indexed by date (ascending) with columns:
        open, high, low, close, volume.
        """
