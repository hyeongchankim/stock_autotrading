"""Sanity checks for the market-wide discovery screener.

Run with: pytest
(or: python -m unittest tests.test_discover)
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from broker.kis_auth import KisSession
from screening.discover import filter_candidates, scan_market


def _env_patch(**overrides):
    base = {
        "KIS_APP_KEY": "real-key",
        "KIS_APP_SECRET": "real-secret",
        "KIS_ACCOUNT_NO": "11111111",
        "KIS_ACCOUNT_PRODUCT_CD": "01",
        "KIS_PAPER_APP_KEY": "paper-key",
        "KIS_PAPER_APP_SECRET": "paper-secret",
        "KIS_PAPER_ACCOUNT_NO": "22222222",
        "KIS_PAPER_ACCOUNT_PRODUCT_CD": "01",
    }
    base.update(overrides)
    return patch.dict("os.environ", base, clear=True)


class TestFilterCandidates(unittest.TestCase):
    def test_keeps_only_universe_members_above_trading_value_floor(self):
        scan = pd.DataFrame({
            "ticker": ["005930", "999999", "000660"],
            "name": ["삼성전자", "장외종목", "SK하이닉스"],
            "price": [70000.0, 10000.0, 5000.0],
            "change_pct": [3.0, 5.0, -2.0],
            "volume": [100000, 1000000, 100],  # 000660: 5000*100=500,000 too small
        })
        universe = {"005930.KS": "삼성전자", "000660.KS": "SK하이닉스"}
        result = filter_candidates(scan, universe, min_trading_value=1_000_000_000)
        self.assertEqual(list(result["symbol"]), ["005930.KS"])

    def test_empty_scan_returns_empty(self):
        scan = pd.DataFrame(columns=["ticker", "name", "price", "change_pct", "volume"])
        result = filter_candidates(scan, {"005930.KS": "삼성전자"})
        self.assertTrue(result.empty)


class TestScanMarket(unittest.TestCase):
    def setUp(self):
        self.env_patcher = _env_patch()
        self.env_patcher.start()
        self.token_patcher = patch("broker.kis_auth.KisSession.get_access_token", return_value="fake-token")
        self.token_patcher.start()
        self.session = KisSession(env="demo")

    def tearDown(self):
        self.token_patcher.stop()
        self.env_patcher.stop()

    @patch("broker.kis_auth.requests.get")
    def test_parses_ranking_response(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output": [
                    {"stck_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "stck_prpr": "70000",
                     "prdy_ctrt": "3.5", "acml_vol": "1000000"},
                ],
            },
        )
        df = scan_market(self.session)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["ticker"], "005930")
        self.assertEqual(df.iloc[0]["price"], 70000.0)
        self.assertEqual(df.iloc[0]["change_pct"], 3.5)

    @patch("broker.kis_auth.requests.get")
    def test_uses_9_and_10_digit_exclusion_codes(self, mock_get):
        # regression check: a single "0"/"1" here silently lets SPAC/ETN
        # noise through instead of raising - see module docstring.
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"rt_cd": "0", "output": []})
        scan_market(self.session)
        sent_params = mock_get.call_args.kwargs["params"]
        self.assertEqual(len(sent_params["fid_trgt_cls_code"]), 9)
        self.assertEqual(len(sent_params["fid_trgt_exls_cls_code"]), 10)

    @patch("broker.kis_auth.requests.get")
    def test_raises_on_api_failure(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "1", "msg1": "조회 실패"}
        )
        with self.assertRaises(RuntimeError):
            scan_market(self.session)


if __name__ == "__main__":
    unittest.main()
