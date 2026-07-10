"""Temporary market data source, used until a real brokerage data API is wired in.

Works for KRX tickers via the .KS / .KQ suffix (e.g. "005930.KS" for Samsung
Electronics) and most global tickers, with no API key required.
"""
from __future__ import annotations

import pandas as pd

from data.base import DataFeedBase


class YFinanceDataFeed(DataFeedBase):
    def get_ohlcv(self, symbol: str, interval: str = "1d", lookback: int = 100) -> pd.DataFrame:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        raw = ticker.history(period="max", interval=interval, auto_adjust=False)
        if raw.empty:
            raise ValueError(f"No data returned for symbol: {symbol}")

        raw = raw.rename(columns=str.lower)
        df = raw[["open", "high", "low", "close", "volume"]].copy()
        df.index.name = "date"
        return df.tail(lookback)
