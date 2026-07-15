"""Shared authentication/session handling for the KIS (한국투자증권) Open API.

Used by both broker/kis_broker.py (orders, balance) and
data/kis_data_feed.py (price history) since they share the same OAuth
token and base URL per environment.

Credentials come only from environment variables - never hardcode them:
  KIS_APP_KEY / KIS_APP_SECRET                       - 실전투자 앱키/시크릿
  KIS_PAPER_APP_KEY / KIS_PAPER_APP_SECRET           - 모의투자 앱키/시크릿
  KIS_ACCOUNT_NO / KIS_ACCOUNT_PRODUCT_CD            - 실전투자 계좌번호 (앞 8자리/뒤 2자리)
  KIS_PAPER_ACCOUNT_NO / KIS_PAPER_ACCOUNT_PRODUCT_CD - 모의투자 계좌번호 (앞 8자리/뒤 2자리)

실전/모의 계좌번호는 서로 다른 별개의 번호다 (KIS가 모의투자 신청 시 가상 계좌번호를
따로 발급함) - 앱키와 마찬가지로 절대 혼용하면 안 된다.

The access token is valid 24h and KIS sends a notification on every fresh
issuance, so it's cached to a local file and reused until it's close to
expiring (mirrors the official sample's read_token()/save_token() pattern).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger("broker.kis_auth")

REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 1.0


def get_with_retry(url: str, headers: dict, params: dict, timeout: int = 10) -> requests.Response:
    """Shared GET helper for all KIS endpoints (balance, price, daily chart).

    KIS's 모의투자(paper) servers occasionally return a transient 5xx - safe
    to retry for GET since these are read-only with no side effects.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            logger.warning("KIS GET %s failed (%s), retrying (%d/%d)", url, exc, attempt + 1, _MAX_RETRIES)
            time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue

        if response.status_code >= 500:
            last_exc = requests.exceptions.HTTPError(f"{response.status_code} server error")
            logger.warning(
                "KIS GET %s returned %s, retrying (%d/%d)", url, response.status_code, attempt + 1, _MAX_RETRIES
            )
            time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue

        response.raise_for_status()
        return response

    raise RuntimeError(f"KIS GET {url} failed after {_MAX_RETRIES} attempts") from last_exc

_TOKEN_CACHE_DIR = Path(__file__).resolve().parent.parent / ".kis_cache"


class KisCredentialsError(RuntimeError):
    pass


class KisSession:
    """One session per environment ("real" or "demo"). Handles token
    issuance/caching and building the headers each API call needs.
    """

    def __init__(self, env: str = "demo"):
        if env not in ("real", "demo"):
            raise ValueError("env must be 'real' or 'demo'")
        self.env = env
        self.base_url = REAL_BASE_URL if env == "real" else PAPER_BASE_URL

        if env == "real":
            self.app_key = os.getenv("KIS_APP_KEY")
            self.app_secret = os.getenv("KIS_APP_SECRET")
            self.account_no = os.getenv("KIS_ACCOUNT_NO")
            self.account_product_cd = os.getenv("KIS_ACCOUNT_PRODUCT_CD", "01")
        else:
            self.app_key = os.getenv("KIS_PAPER_APP_KEY")
            self.app_secret = os.getenv("KIS_PAPER_APP_SECRET")
            self.account_no = os.getenv("KIS_PAPER_ACCOUNT_NO")
            self.account_product_cd = os.getenv("KIS_PAPER_ACCOUNT_PRODUCT_CD", "01")

        if not self.app_key or not self.app_secret:
            key_var = "KIS_APP_KEY/KIS_APP_SECRET" if env == "real" else "KIS_PAPER_APP_KEY/KIS_PAPER_APP_SECRET"
            raise KisCredentialsError(
                f"Missing {key_var} environment variables for env={env!r}."
            )

        if not self.account_no:
            acct_var = "KIS_ACCOUNT_NO" if env == "real" else "KIS_PAPER_ACCOUNT_NO"
            raise KisCredentialsError(
                f"Missing {acct_var} environment variable for env={env!r}. "
                "Real and paper trading use different account numbers - don't reuse one for the other."
            )

        self._access_token: str | None = None
        self._token_expiry: float = 0.0
        self._market_data_session: KisSession | None = None

    def market_data_session(self) -> "KisSession":
        """Session to use for market-data endpoints (current price, daily
        chart) - KIS only reliably serves these through the real domain
        regardless of account type, confirmed empirically: 8 repeated calls
        to the daily-chart endpoint through the demo domain had a 500 error
        (12.5%), the same calls through the real domain had none, matching
        the actively-maintained python-kis library's consistent choice to
        always route every quote/chart call through the real domain even
        for virtual accounts. Order placement/balance/fills still use this
        session's own env unchanged - only read-only market data is
        affected. Requires KIS_APP_KEY/KIS_APP_SECRET (real credentials) to
        be configured even when only running paper trading - a dependency
        this introduces that didn't exist before.
        """
        if self.env == "real":
            return self
        if self._market_data_session is None:
            self._market_data_session = KisSession(env="real")
        return self._market_data_session

    def _token_cache_path(self) -> Path:
        return _TOKEN_CACHE_DIR / f"token_{self.env}.json"

    def _load_cached_token(self) -> str | None:
        path = self._token_cache_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        expiry = data.get("expiry", 0)
        if expiry - time.time() < 600:  # refresh with 10 min of headroom
            return None
        self._token_expiry = expiry
        return data.get("access_token")

    def _save_token_cache(self, access_token: str, expiry: float) -> None:
        _TOKEN_CACHE_DIR.mkdir(exist_ok=True)
        path = self._token_cache_path()
        path.write_text(json.dumps({"access_token": access_token, "expiry": expiry}), encoding="utf-8")

    def get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expiry - 600:
            return self._access_token

        cached = self._load_cached_token()
        if cached:
            self._access_token = cached
            return cached

        response = requests.post(
            f"{self.base_url}/oauth2/tokenP",
            headers={"Content-Type": "application/json"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=10,
        )
        if response.status_code != 200:
            raise RuntimeError(f"KIS token issuance failed: {response.status_code} {response.text}")

        body = response.json()
        access_token = body["access_token"]
        expires_in = float(body.get("expires_in", 86400))
        expiry = time.time() + expires_in

        self._access_token = access_token
        self._token_expiry = expiry
        self._save_token_cache(access_token, expiry)
        return access_token

    def headers(self, tr_id: str) -> dict:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_access_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def real_tr_id(self, tr_id_real: str) -> str:
        """KIS convention: paper-trading tr_id is the same code with the
        leading letter swapped to 'V' (e.g. TTTC0012U -> VTTC0012U).
        """
        if self.env == "demo" and tr_id_real[0] in ("T", "J", "C"):
            return "V" + tr_id_real[1:]
        return tr_id_real
