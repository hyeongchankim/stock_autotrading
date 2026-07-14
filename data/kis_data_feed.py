"""Real KIS (한국투자증권) daily price data source.

Meant for live/paper trading (main.py's run_paper), where only a small
recent window is needed per cycle. Not meant for bulk backtesting - use
YFinanceDataFeed for that (no rate limits, much faster for years of
history across many symbols). This feed pages backward in ~100-row chunks
(KIS's per-call limit) with a short sleep between pages to stay under the
API's rate limit.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd

from broker.kis_auth import KisSession, get_with_retry
from data.base import DataFeedBase

_DAILY_PRICE_TR_ID = "FHKST03010100"  # same code for both real and paper
_PAGE_SLEEP_SECONDS = 0.3


def _to_kis_ticker(symbol: str) -> str:
    """Converts a yfinance-style symbol (e.g. "005930.KS") to the bare
    6-digit KIS/KRX ticker (e.g. "005930")."""
    return symbol.split(".")[0]


class KisDataFeed(DataFeedBase):
    def __init__(self, env: str = "demo", session: KisSession | None = None):
        self.session = session or KisSession(env=env)

    def _endpoint(self) -> str:
        return f"{self.session.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"

    def get_ohlcv(self, symbol: str, interval: str = "1d", lookback: int = 100) -> pd.DataFrame:
        if interval != "1d":
            raise ValueError("KisDataFeed only supports daily bars (interval='1d')")

        ticker = _to_kis_ticker(symbol)
        # generous calendar-day span so `lookback` trading days are covered
        start_date = date.today() - timedelta(days=int(lookback * 1.6) + 10)
        start_str = start_date.strftime("%Y%m%d")
        current_end = date.today().strftime("%Y%m%d")

        rows: list[dict] = []
        while True:
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": start_str,
                "FID_INPUT_DATE_2": current_end,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            }
            response = get_with_retry(self._endpoint(), self.session.headers(_DAILY_PRICE_TR_ID), params)
            body = response.json()
            if body.get("rt_cd") != "0":
                raise RuntimeError(f"KIS daily price fetch failed for {symbol}: {body.get('msg1')}")

            page = body.get("output2", [])
            if not page:
                break
            rows.extend(page)

            last_date = page[-1].get("stck_bsop_date", "")
            if not last_date or last_date <= start_str or len(page) < 100 or len(rows) >= lookback:
                break

            current_end = (pd.Timestamp(last_date) - pd.Timedelta(days=1)).strftime("%Y%m%d")
            time.sleep(_PAGE_SLEEP_SECONDS)

        if not rows:
            raise ValueError(f"No data returned for symbol: {symbol}")

        df = pd.DataFrame(
            {
                "date": [r["stck_bsop_date"] for r in rows],
                "open": [float(r["stck_oprc"]) for r in rows],
                "high": [float(r["stck_hgpr"]) for r in rows],
                "low": [float(r["stck_lwpr"]) for r in rows],
                "close": [float(r["stck_clpr"]) for r in rows],
                "volume": [int(r["acml_vol"]) for r in rows],
            }
        )
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        df = df.drop_duplicates(subset="date").sort_values("date").set_index("date")
        return df.tail(lookback)
