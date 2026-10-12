"""Today's candidate list computed from the universe itself, instead of KIS's ranking page.

Why: `scan_market` (KIS 등락률 순위, tr_id FHPST01700000) returns ONE page of 30 rows that is neither sorted nor a
"top gainers" list - on 2026-10-12 the 30 rows ranged -10.4%..+1.1% (median -4.0%), so the 발굴형 and dynamic-watchlist
candidates were an arbitrary 30 names, mostly decliners. This module asks KIS for the live quote of every universe
member (multi-stock quote, tr_id FHKST11300006, 30 symbols per call -> ~12 calls / a few seconds for the ~350-name
KOSPI200+KOSDAQ150 universe) and ranks them by their exact change % ourselves.

Verified against the live API on 2026-10-12: fields inter2_prpr (price), prdy_ctrt (change %), acml_vol, acml_tr_pbmn
(exact 거래대금 in KRW - the ranking API only allowed price*volume), inter2_oprc/hgpr/lwpr, inter2_prdy_clpr.

Dropped vs the ranking API: its server-side exclusion flags (관리종목/투자경고/ETF/SPAC/우선주...). Universe members are
index constituents, and a halted stock shows zero turnover, so the 거래대금 floor removes it.
"""
from __future__ import annotations

import logging

import pandas as pd

from broker.kis_auth import KisSession, get_with_retry

logger = logging.getLogger("screening.universe_quotes")

_MULTI_PRICE_TR_ID = "FHKST11300006"  # same code for real and paper; always served via the real domain
BATCH = 30  # the endpoint's per-call limit

COLUMNS = ["ticker", "name", "price", "change_pct", "volume", "trading_value", "open", "high", "prev_close"]


def fetch_quotes(session: KisSession, tickers: list[str]) -> pd.DataFrame:
    """Live quotes for bare 6-digit tickers, `BATCH` per request. Raises if any batch fails after the shared GET
    retries - a silently incomplete universe would skew the ranking, so callers decide how to degrade."""
    md = session.market_data_session()
    url = f"{md.base_url}/uapi/domestic-stock/v1/quotations/intstock-multprice"
    rows: list[dict] = []
    for i in range(0, len(tickers), BATCH):
        params: dict[str, str] = {}
        for n, ticker in enumerate(tickers[i:i + BATCH], 1):
            params[f"FID_COND_MRKT_DIV_CODE_{n}"] = "J"
            params[f"FID_INPUT_ISCD_{n}"] = ticker
        body = get_with_retry(url, md.headers(_MULTI_PRICE_TR_ID), params).json()
        if body.get("rt_cd") != "0":
            raise RuntimeError(f"KIS multi-stock quote failed: {body.get('msg1')}")
        for r in body.get("output", []):
            rows.append({
                "ticker": r["inter_shrn_iscd"], "name": r["inter_kor_isnm"],
                "price": float(r["inter2_prpr"]), "change_pct": float(r["prdy_ctrt"]),
                "volume": int(r["acml_vol"]), "trading_value": float(r["acml_tr_pbmn"]),
                "open": float(r["inter2_oprc"]), "high": float(r["inter2_hgpr"]),
                "prev_close": float(r["inter2_prdy_clpr"]),
            })
    return pd.DataFrame(rows, columns=COLUMNS)


def screen_candidates(
    quotes: pd.DataFrame, universe: dict[str, str], min_price: float, max_change_pct: float, min_trading_value: float,
) -> pd.DataFrame:
    """The discovery 1차 필터 on exact quotes, strongest first. `universe` maps yfinance-style symbols to tickers
    (only its keys are used to attach `symbol`). Returns discover.filter_candidates' columns."""
    to_symbol = {s.split(".")[0]: s for s in universe}
    df = quotes.copy()
    df["symbol"] = df["ticker"].map(to_symbol)
    df = df[
        df["symbol"].notna()
        & (df["price"] >= min_price)
        & (df["change_pct"].abs() <= max_change_pct)
        & (df["trading_value"] >= min_trading_value)
    ]
    return df.sort_values("change_pct", ascending=False).reset_index(drop=True)


def universe_candidates(session: KisSession, universe: dict[str, str], disc_cfg: dict) -> pd.DataFrame:
    """Today's candidates: every universe member's live quote -> 1차 필터 -> sorted by change % (desc)."""
    tickers = [s.split(".")[0] for s in universe]
    quotes = fetch_quotes(session, tickers)
    if len(quotes) < 0.9 * len(tickers):  # KIS silently dropping symbols would skew the ranking
        raise RuntimeError(f"multi-stock quote returned {len(quotes)} of {len(tickers)} universe symbols")
    return screen_candidates(
        quotes, universe, disc_cfg["min_price"], disc_cfg["max_change_pct"], disc_cfg["min_trading_value"],
    )
