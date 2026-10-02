"""Market-wide daily candidate discovery for the '종목 발굴형 종가매매' sleeve
- screens KOSPI200+KOSDAQ150 instead of trading a fixed watchlist.

Pipeline (see main.py's run_discovery / config.yaml's `discovery` section):
1. get_universe() - KOSPI200+KOSDAQ150 constituent tickers (pykrx, disk-cached)
2. scan_market() - one KIS API call ranking the whole market by today's
   price change, with exclusion flags already applied server-side
3. filter_candidates() - intersect with the universe and apply the
   trading-value floor (1차 필터)
4. checkpoint persistence (checkpoint_store.py) - candidates must show up
   across multiple scans during the day, not just once, to survive to a
   final decision

tr_id FHPST01700000 (국내주식 등락률 순위) was verified against the real KIS
demo account on 2026-09-30 - see that session's notes before trusting any
of the fid_* parameter meanings blindly (this project's own precedent:
reference-repo docstrings for KIS tr_ids/params have been wrong before).
Notably fid_trgt_cls_code/fid_trgt_exls_cls_code are fixed-width digit
strings (9 and 10 digits) - passing a single "0" silently lets SPAC/ETN
noise through instead of raising an error.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from broker.kis_auth import KisSession, get_with_retry

logger = logging.getLogger("screening.discover")

_FLUCTUATION_TR_ID = "FHPST01700000"  # same code for real and paper - see module docstring
_FLUCTUATION_SCR_DIV_CODE = "20170"


UNIVERSE_CACHE = Path(__file__).resolve().parent.parent / "universe_cache.json"
_MIN_UNIVERSE_SIZE = 200  # KOSPI200+KOSDAQ150 is ~350; far fewer means a bad/partial KRX response


def _fetch_universe() -> dict[str, str]:
    """KOSPI200 (index "1028") + KOSDAQ150 ("2203") constituents straight from
    KRX (pykrx - needs KRX_ID/KRX_PW; importing pykrx itself logs in). Names are
    not looked up (nothing uses them, and it would be ~350 extra KRX calls), so
    the value is just the ticker code."""
    from pykrx import stock

    universe: dict[str, str] = {}
    for index_code, suffix in (("1028", ".KS"), ("2203", ".KQ")):
        for ticker in stock.get_index_portfolio_deposit_file(index_code):
            universe[f"{ticker}{suffix}"] = ticker
    if len(universe) < _MIN_UNIVERSE_SIZE:
        raise ValueError(f"KRX returned only {len(universe)} index constituents")
    return universe


def get_universe(max_age_days: int = 7, cache_path: Path = UNIVERSE_CACHE) -> dict[str, str]:
    """{yfinance-style symbol: ticker} for the discovery universe, cached on disk.

    The index changes only a few times a year, but this used to hit KRX on every
    discovery run (3x/day) - and on 2026-10-02 KRX banned the PC's IP for a day for
    automated query volume (see CONTEXT_HANDOFF). So: a cache younger than
    `max_age_days` is used without touching KRX at all; an older one is refreshed,
    and if the refresh fails (outage, ban) the stale cache is used with a warning
    rather than stopping the sleeve. With no cache and no KRX, the error propagates.
    """
    cached = _load_universe_cache(cache_path)
    if cached is not None:
        fetched_on, universe = cached
        if (date.today() - fetched_on).days <= max_age_days:
            return universe
    try:
        fresh = _fetch_universe()
    except Exception as exc:  # noqa: BLE001 - any KRX failure falls back to the cache
        if cached is None:
            raise
        logger.warning(
            "KRX universe refresh failed (%s) - using the cache from %s (%d days old)",
            exc, cached[0], (date.today() - cached[0]).days,
        )
        return cached[1]
    cache_path.write_text(
        json.dumps({"fetched_on": date.today().isoformat(), "universe": fresh}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return fresh


def _load_universe_cache(path: Path) -> tuple[date, dict[str, str]] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        universe = raw["universe"]
        if len(universe) < _MIN_UNIVERSE_SIZE:
            return None
        return date.fromisoformat(raw["fetched_on"]), universe
    except (OSError, ValueError, KeyError, TypeError):
        return None


def scan_market(
    session: KisSession, min_price: int = 5000, max_change_pct: float = 15.0, top_n: int = 200,
) -> pd.DataFrame:
    """One KIS call scanning the whole market (fid_input_iscd="0000"),
    server-side filtered to >= min_price, |change%| <= max_change_pct, and
    excluding 관리종목/투자경고/정리매매/불성실공시/우선주/거래정지/ETF/ETN/
    신용주문불가/SPAC (all 10 fid_trgt_exls_cls_code flags set).

    The server-side sort order for this tr_id isn't reliably documented
    (tested empirically, no fid_rank_sort_cls_code value gave a value
    consistently sorted by change_pct) - callers should treat `top_n` as
    "a broad enough sample to then filter/sort ourselves", not as
    pre-sorted top movers.

    Returns columns: ticker (6-digit), name, price, change_pct, volume.
    """
    md_session = session.market_data_session()
    url = f"{md_session.base_url}/uapi/domestic-stock/v1/ranking/fluctuation"
    params = {
        "fid_rsfl_rate2": str(max_change_pct),
        "fid_cond_mrkt_div_code": "J",
        "fid_cond_scr_div_code": _FLUCTUATION_SCR_DIV_CODE,
        "fid_input_iscd": "0000",
        "fid_rank_sort_cls_code": "0",
        "fid_input_cnt_1": str(top_n),
        "fid_prc_cls_code": "0",
        "fid_input_price_1": str(min_price),
        "fid_input_price_2": "",
        "fid_vol_cnt": "",
        "fid_trgt_cls_code": "0" * 9,
        "fid_trgt_exls_cls_code": "1" * 10,
        "fid_div_cls_code": "0",
        "fid_rsfl_rate1": str(-max_change_pct),
    }
    response = get_with_retry(url, md_session.headers(_FLUCTUATION_TR_ID), params)
    body = response.json()
    if body.get("rt_cd") != "0":
        raise RuntimeError(f"KIS fluctuation ranking failed: {body.get('msg1')}")

    rows = body.get("output", [])
    if not rows:
        return pd.DataFrame(columns=["ticker", "name", "price", "change_pct", "volume"])

    return pd.DataFrame({
        "ticker": [r["stck_shrn_iscd"] for r in rows],
        "name": [r["hts_kor_isnm"] for r in rows],
        "price": [float(r["stck_prpr"]) for r in rows],
        "change_pct": [float(r["prdy_ctrt"]) for r in rows],
        "volume": [int(r["acml_vol"]) for r in rows],
    })


def filter_candidates(
    scan: pd.DataFrame, universe: dict[str, str], min_trading_value: float = 3_000_000_000,
) -> pd.DataFrame:
    """1차 필터: keep only rows inside the KOSPI200+KOSDAQ150 universe with
    approximated trading value (price * volume, KRW - the fluctuation
    endpoint has no exact 거래대금 field) above min_trading_value.
    """
    df = scan.copy()
    df["symbol"] = df["ticker"].map(
        lambda t: f"{t}.KS" if f"{t}.KS" in universe else (f"{t}.KQ" if f"{t}.KQ" in universe else None)
    )
    df = df[df["symbol"].notna()].copy()
    df["trading_value"] = df["price"] * df["volume"]
    df = df[df["trading_value"] >= min_trading_value]
    return df.reset_index(drop=True)
