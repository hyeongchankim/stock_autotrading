"""Foreign/institutional investor net-buy data source (KRX Information Data
System, data.krx.co.kr). Requires a free data.krx.co.kr account - set
KRX_ID and KRX_PW as environment variables so pykrx can log in
automatically. Never hardcode credentials here or anywhere else in this
project.

This is optional data: callers should catch fetch failures (missing
credentials, network issues) and proceed without it - WhaleFlowStrategy
holds when its columns aren't present rather than erroring.
"""
from __future__ import annotations

import logging

import pandas as pd

from data.base import DataFeedBase

logger = logging.getLogger("data.krx_investor_feed")


def _to_krx_ticker(symbol: str) -> str:
    """Converts a yfinance-style symbol (e.g. "005930.KS") to the bare
    6-digit KRX ticker pykrx expects (e.g. "005930")."""
    return symbol.split(".")[0]


class KrxInvestorFlowFeed:
    def get_net_buy_value(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Returns a DataFrame indexed by date with columns:
        institutional_net, foreign_net (원화 순매수 금액, KRW).

        start_date/end_date must be "YYYYMMDD" strings. symbol may be a
        yfinance-style ".KS"/".KQ" ticker or a bare 6-digit KRX code.
        """
        from pykrx import stock

        ticker = _to_krx_ticker(symbol)
        raw = stock.get_market_trading_value_by_date(start_date, end_date, ticker)
        if raw.empty:
            raise ValueError(
                f"No investor flow data returned for {symbol}. "
                "Check that KRX_ID/KRX_PW environment variables are set and valid."
            )

        df = pd.DataFrame(
            {
                "institutional_net": raw["기관합계"],
                "foreign_net": raw["외국인합계"],
            }
        )
        df.index = pd.to_datetime(df.index)
        df.index.name = "date"
        return df


def attach_investor_flow(ohlcv: pd.DataFrame, flow: pd.DataFrame) -> pd.DataFrame:
    """Left-joins institutional_net/foreign_net columns onto an OHLCV frame
    by date, normalizing both indices to tz-naive dates first since the
    yfinance feed's index is tz-aware and pykrx's is not.
    """
    merged = ohlcv.copy()
    normalized_index = merged.index.tz_localize(None) if merged.index.tz is not None else merged.index

    flow_aligned = flow.reindex(normalized_index)
    merged["institutional_net"] = flow_aligned["institutional_net"].to_numpy()
    merged["foreign_net"] = flow_aligned["foreign_net"].to_numpy()
    return merged


class WhaleEnrichedDataFeed(DataFeedBase):
    """Wraps a base price data feed and adds institutional_net/foreign_net
    columns from KRX investor flow data after each fetch, so WhaleFlowStrategy
    has what it needs without any other code (engine, backtest runner) having
    to know investor-flow data exists.

    Degrades gracefully: if the KRX fetch fails for a symbol (missing
    KRX_ID/KRX_PW, network issue, delisted ticker, etc.), returns the plain
    OHLCV frame unchanged and logs a warning - WhaleFlowStrategy simply holds
    for that symbol since its columns are absent.
    """

    def __init__(self, base_feed: DataFeedBase, investor_feed: KrxInvestorFlowFeed | None = None):
        self.base_feed = base_feed
        self.investor_feed = investor_feed or KrxInvestorFlowFeed()

    def get_ohlcv(self, symbol: str, interval: str = "1d", lookback: int = 100) -> pd.DataFrame:
        ohlcv = self.base_feed.get_ohlcv(symbol, interval, lookback)
        try:
            start_date = ohlcv.index.min().strftime("%Y%m%d")
            end_date = ohlcv.index.max().strftime("%Y%m%d")
            flow = self.investor_feed.get_net_buy_value(symbol, start_date, end_date)
            return attach_investor_flow(ohlcv, flow)
        except Exception as exc:
            logger.warning("skipping whale flow enrichment for %s: %s", symbol, exc)
            return ohlcv
