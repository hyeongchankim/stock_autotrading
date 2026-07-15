"""Real KIS (한국투자증권) broker adapter - places actual orders through the
Open API. Defaults to env="demo" (모의투자/paper trading); env="real" places
real-money orders and should only be used after the strategy has been
validated in paper trading for a meaningful period.

tr_id codes below (TTTC0802U/TTTC0801U for cash buy/sell, TTTC8434R for
balance) match the constants used in KIS's own backtester reference module
(backtester/kis_backtest/providers/kis/constants.py in
koreainvestment/open-trading-api) - note that a different sample file in
the same repo (examples_user/domestic_stock/domestic_stock_functions.py)
uses TTTC0011U/0012U for the same endpoint, which appears to be inconsistent
with that repo's own docstrings and the backtester module. Verify against
current KIS Developers documentation before relying on this in production.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date

import requests

from broker.base import BrokerBase, OrderResult
from broker.kis_auth import KisSession, get_with_retry
from broker.krx_tick import round_to_tick
from portfolio.portfolio import Position
from strategies.base import Signal

logger = logging.getLogger("broker.kis_broker")

_BUY_TR_ID_REAL = "TTTC0802U"
_SELL_TR_ID_REAL = "TTTC0801U"
_BALANCE_TR_ID_REAL = "TTTC8434R"
_PRICE_TR_ID = "FHKST01010100"  # same code for both real and paper
_DAILY_FILLS_TR_ID_REAL = "TTTC0081R"  # only covers the last 3 months - fine for this bot's needs

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 1.0


@dataclass
class OrderFill:
    """One row from KIS's 주식일별주문체결조회 (get_daily_fills) - the actual
    execution status of a previously placed order. place_order()'s
    filled=True only means KIS accepted the order, not that it fully
    executed: a 지정가(limit) order can sit partially or wholly unfilled.
    """
    order_no: str
    symbol: str
    ordered_qty: int
    filled_qty: int
    pending_qty: int
    avg_fill_price: float


def _to_kis_ticker(symbol: str) -> str:
    """Converts a yfinance-style symbol (e.g. "005930.KS") to the bare
    6-digit KIS/KRX ticker (e.g. "005930")."""
    return symbol.split(".")[0]


def _post_no_ambiguous_retry(url: str, headers: dict, json_body: dict, timeout: int = 10) -> requests.Response:
    """For order placement. Only retries connection-level failures where the
    request never reached the server - never retries on a 5xx response,
    since a 5xx there means we can't tell whether the order was actually
    accepted before the error was returned. Silently retrying could submit
    a duplicate order, so an ambiguous 5xx is surfaced as a failure instead.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return requests.post(url, headers=headers, json=json_body, timeout=timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            logger.warning(
                "KIS POST %s connection failed (%s), retrying (%d/%d)", url, exc, attempt + 1, _MAX_RETRIES
            )
            time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))

    raise RuntimeError(f"KIS POST {url} failed after {_MAX_RETRIES} attempts") from last_exc


class KisBroker(BrokerBase):
    def __init__(
        self,
        env: str = "demo",
        watchlist: list[str] | None = None,
        seed_capital: float | None = None,
        commission_pct: float = 0.0,
        sell_tax_pct: float = 0.0,
    ):
        """seed_capital, if given, switches get_cash_balance()/get_total_equity()
        to a locally tracked cash ledger scoped to that amount instead of the
        real account's actual balance (e.g. a fresh KIS demo account starts
        with its own default balance, unrelated to this bot's configured
        seed_capital - without this, RiskManager.calc_position_size would
        size positions off that real balance and massively overshoot what
        was backtested). Positions/avg_price still always come from the real
        account, which stays accurate as long as nothing outside this bot's
        own BUY/SELL calls touches it - the hybrid buy_and_hold sleeve is
        local-only and never places real orders (portfolio/buy_and_hold.py).
        Leave seed_capital=None to fall back to reading the real balance
        directly (e.g. for one-off account inspection).

        commission_pct/sell_tax_pct (percentages, e.g. 0.015 means 0.015%)
        are applied to the ledger the same way MockBroker applies them to
        its Portfolio - the real KIS account deducts its own fees
        automatically regardless of what we compute here, this just keeps
        the local ledger's approximation of "what the strategy sleeve
        actually has to spend" from drifting away from it. Real orders are
        still sent at the raw fill_price - KIS, not this ledger, decides
        the account's real fees.
        """
        self.session = KisSession(env=env)
        # bare ticker -> full yfinance-style symbol, so get_positions() keys
        # match what the rest of the engine (watchlist strings) expects
        self._ticker_to_symbol = {_to_kis_ticker(s): s for s in (watchlist or [])}
        self._cash_ledger: float | None = seed_capital
        self.commission_pct = commission_pct
        self.sell_tax_pct = sell_tax_pct

    def ledger_to_dict(self) -> dict:
        """Snapshot for StateStore, so the local cash ledger survives across
        separate process runs. Empty when no seed_capital was configured.
        """
        return {"cash": self._cash_ledger} if self._cash_ledger is not None else {}

    def restore_ledger(self, state: dict) -> None:
        if self._cash_ledger is None or not state:
            return
        self._cash_ledger = state.get("cash", self._cash_ledger)

    def _resolve_symbol(self, bare_ticker: str) -> str:
        return self._ticker_to_symbol.get(bare_ticker, bare_ticker)

    def _order_endpoint(self) -> str:
        return f"{self.session.base_url}/uapi/domestic-stock/v1/trading/order-cash"

    def _daily_fills_endpoint(self) -> str:
        return f"{self.session.base_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"

    def _balance_endpoint(self) -> str:
        return f"{self.session.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"

    def _price_endpoint(self, session: KisSession) -> str:
        return f"{session.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"

    def _inquire_balance(self) -> tuple[list[dict], list[dict]]:
        tr_id = self.session.real_tr_id(_BALANCE_TR_ID_REAL)
        params = {
            "CANO": self.session.account_no,
            "ACNT_PRDT_CD": self.session.account_product_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        response = get_with_retry(self._balance_endpoint(), self.session.headers(tr_id), params)
        body = response.json()
        if body.get("rt_cd") != "0":
            raise RuntimeError(f"KIS balance inquiry failed: {body.get('msg1')}")
        return body.get("output1", []), body.get("output2", [{}])

    def get_cash_balance(self) -> float:
        if self._cash_ledger is not None:
            return self._cash_ledger
        _holdings, summary = self._inquire_balance()
        if not summary:
            return 0.0
        return float(summary[0].get("dnca_tot_amt", 0))

    def get_positions(self) -> dict:
        holdings, _summary = self._inquire_balance()
        positions = {}
        for item in holdings:
            qty = int(item.get("hldg_qty", 0))
            if qty <= 0:
                continue
            symbol = self._resolve_symbol(item.get("pdno", ""))
            positions[symbol] = Position(
                symbol=symbol,
                quantity=qty,
                avg_price=float(item.get("pchs_avg_pric", 0)),
            )
        return positions

    def get_current_price(self, symbol: str) -> float:
        # market_data_session(): quote endpoints are only reliably served
        # through the real domain, even for a demo account - see that
        # method's docstring.
        md_session = self.session.market_data_session()
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": _to_kis_ticker(symbol)}
        response = get_with_retry(self._price_endpoint(md_session), md_session.headers(_PRICE_TR_ID), params)
        body = response.json()
        if body.get("rt_cd") != "0":
            raise RuntimeError(f"KIS price inquiry failed for {symbol}: {body.get('msg1')}")
        return float(body["output"]["stck_prpr"])

    def get_daily_fills(self, order_no: str = "", as_of: date | None = None) -> list[OrderFill]:
        """Queries actual execution status via 주식일별주문체결조회
        (inquire-daily-ccld) - covers only the last 3 months, which is all
        this bot needs (it never holds an order open that long). Pass
        order_no to check one specific order; leave blank to list every
        order for as_of's date (defaults to today).

        tr_id here is TTTC0081R/VTTC0081R (examples_user/domestic_stock/
        domestic_stock_functions.py's inquire_daily_ccld, env_dv+pd_dv="inner"),
        confirmed empirically against a real KIS demo order - NOT
        TTTC8001R/VTTC8001R, which is what KIS's own backtester reference
        module declares (backtester/kis_backtest/providers/kis/constants.py)
        and what the actively-maintained third-party python-kis library
        (Soju06/python-kis) also uses. Both of those looked more trustworthy
        going in (two independent sources agreeing, vs. examples_user which
        this file's own docstring already flags as internally inconsistent
        elsewhere) - but VTTC8001R returned rt_cd="0" with a genuinely empty
        output1 against the real demo account (output2's aggregate totals
        were populated, so the account/params were fine - just no
        per-order rows), while VTTC0081R returned the actual order the demo
        account had just filled, fields matching exactly what's parsed
        below. Lesson: cross-referencing multiple sources up front reduces
        risk but doesn't replace testing against the real API before
        trusting a tr_id - see place_order()'s similar TTTC0801U/0802U
        situation, which happened to test out fine on the first guess.
        """
        as_of = as_of or date.today()
        tr_id = self.session.real_tr_id(_DAILY_FILLS_TR_ID_REAL)
        date_str = as_of.strftime("%Y%m%d")
        params = {
            "CANO": self.session.account_no,
            "ACNT_PRDT_CD": self.session.account_product_cd,
            "INQR_STRT_DT": date_str,
            "INQR_END_DT": date_str,
            "SLL_BUY_DVSN_CD": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "INQR_DVSN": "00",
            "INQR_DVSN_3": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": order_no,
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        response = get_with_retry(self._daily_fills_endpoint(), self.session.headers(tr_id), params)
        body = response.json()
        if body.get("rt_cd") != "0":
            raise RuntimeError(f"KIS daily fill inquiry failed: {body.get('msg1')}")

        fills = []
        for item in body.get("output1", []):
            fills.append(
                OrderFill(
                    order_no=item.get("odno", ""),
                    symbol=self._resolve_symbol(item.get("pdno", "")),
                    ordered_qty=int(item.get("ord_qty", 0)),
                    filled_qty=int(item.get("tot_ccld_qty", 0)),
                    pending_qty=int(item.get("rmn_qty", 0)),
                    avg_fill_price=float(item.get("avg_prvs") or 0),
                )
            )
        return fills

    def _effective_price(self, side: Signal, fill_price: float) -> float:
        """fill_price adjusted for commission_pct (both sides) and
        sell_tax_pct (sell only) - mirrors MockBroker/Portfolio's cost
        model, used only for the local ledger/realized_pnl approximation
        (see __init__ docstring), never sent to KIS as the order price.
        """
        if side == Signal.BUY:
            return fill_price * (1 + self.commission_pct / 100)
        return fill_price * (1 - self.commission_pct / 100 - self.sell_tax_pct / 100)

    def place_order(
        self, symbol: str, side: Signal, quantity: int, price: float | None = None
    ) -> OrderResult:
        if quantity <= 0:
            return OrderResult(symbol, side, quantity, price or 0.0, False, "quantity must be positive")

        ticker = _to_kis_ticker(symbol)
        fill_price = round_to_tick(price if price is not None else self.get_current_price(symbol))
        realized_pnl = 0.0

        if side == Signal.BUY:
            tr_id = self.session.real_tr_id(_BUY_TR_ID_REAL)
        elif side == Signal.SELL:
            tr_id = self.session.real_tr_id(_SELL_TR_ID_REAL)
            position = self.get_positions().get(symbol)
            if position is not None:
                realized_pnl = (self._effective_price(side, fill_price) - position.avg_price) * quantity
        else:
            return OrderResult(symbol, side, quantity, fill_price, False, "HOLD is not an order side")

        if self.session.env == "real":
            logger.warning(
                "PLACING REAL-MONEY ORDER: %s %s x%s @ %.0f", side.value, symbol, quantity, fill_price
            )

        body = {
            "CANO": self.session.account_no,
            "ACNT_PRDT_CD": self.session.account_product_cd,
            "PDNO": ticker,
            "ORD_DVSN": "00",  # 지정가 (limit order at fill_price)
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(int(fill_price)),
        }
        response = _post_no_ambiguous_retry(self._order_endpoint(), self.session.headers(tr_id), body)
        if response.status_code >= 500:
            return OrderResult(
                symbol, side, quantity, fill_price, False,
                f"KIS server error {response.status_code} - order status unknown, "
                "check the account directly before retrying (do not assume it failed)",
            )
        response.raise_for_status()
        result_body = response.json()

        if result_body.get("rt_cd") != "0":
            return OrderResult(symbol, side, quantity, fill_price, False, result_body.get("msg1", "order failed"))

        order_no = result_body.get("output", {}).get("ODNO", "")
        if self._cash_ledger is not None:
            effective_price = self._effective_price(side, fill_price)
            if side == Signal.BUY:
                self._cash_ledger -= quantity * effective_price
            else:
                self._cash_ledger += quantity * effective_price
        return OrderResult(symbol, side, quantity, fill_price, True, f"filled (order_no={order_no})", realized_pnl)
