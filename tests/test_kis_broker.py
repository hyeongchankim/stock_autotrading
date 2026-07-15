"""Sanity checks for the KIS broker adapter and its auth session, using
mocked HTTP responses - no real network calls or KRX/KIS credentials
needed to run this suite.

Run with: pytest
(or: python -m unittest tests.test_kis_broker)
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from broker.kis_auth import KisCredentialsError, KisSession
from broker.kis_broker import KisBroker, _to_kis_ticker
from strategies.base import Signal


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


class TestTickerConversion(unittest.TestCase):
    def test_strips_exchange_suffix(self):
        self.assertEqual(_to_kis_ticker("005930.KS"), "005930")
        self.assertEqual(_to_kis_ticker("058470.KQ"), "058470")


class TestKisSession(unittest.TestCase):
    def test_missing_credentials_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(KisCredentialsError):
                KisSession(env="demo")

    def test_real_tr_id_stays_real_in_real_env(self):
        with _env_patch():
            session = KisSession(env="real")
            self.assertEqual(session.real_tr_id("TTTC0802U"), "TTTC0802U")

    def test_real_tr_id_swaps_prefix_in_demo_env(self):
        with _env_patch():
            session = KisSession(env="demo")
            self.assertEqual(session.real_tr_id("TTTC0802U"), "VTTC0802U")

    def test_rejects_invalid_env(self):
        with _env_patch():
            with self.assertRaises(ValueError):
                KisSession(env="paper")

    def test_real_and_demo_use_separate_account_numbers(self):
        with _env_patch():
            real_session = KisSession(env="real")
            demo_session = KisSession(env="demo")
            self.assertEqual(real_session.account_no, "11111111")
            self.assertEqual(demo_session.account_no, "22222222")
            self.assertNotEqual(real_session.account_no, demo_session.account_no)

    def test_demo_missing_paper_account_no_raises(self):
        with _env_patch(KIS_PAPER_ACCOUNT_NO=""):
            with self.assertRaises(KisCredentialsError):
                KisSession(env="demo")


class TestKisBroker(unittest.TestCase):
    def setUp(self):
        self.env_patcher = _env_patch()
        self.env_patcher.start()
        self.token_patcher = patch("broker.kis_auth.KisSession.get_access_token", return_value="fake-token")
        self.token_patcher.start()
        self.broker = KisBroker(env="demo", watchlist=["005930.KS"])

    def tearDown(self):
        self.token_patcher.stop()
        self.env_patcher.stop()

    @patch("broker.kis_auth.requests.get")
    def test_get_cash_balance_parses_dnca_tot_amt(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "543210"}]},
        )
        self.assertEqual(self.broker.get_cash_balance(), 543210.0)

    @patch("broker.kis_auth.requests.get")
    def test_get_positions_skips_zero_quantity_and_resolves_symbol(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [
                    {"pdno": "005930", "hldg_qty": "3", "pchs_avg_pric": "70000"},
                    {"pdno": "999999", "hldg_qty": "0", "pchs_avg_pric": "0"},
                ],
                "output2": [{"dnca_tot_amt": "0"}],
            },
        )
        positions = self.broker.get_positions()
        self.assertEqual(set(positions.keys()), {"005930.KS"})
        self.assertEqual(positions["005930.KS"].quantity, 3)
        self.assertEqual(positions["005930.KS"].avg_price, 70000.0)

    @patch("broker.kis_auth.requests.get")
    def test_get_current_price_parses_stck_prpr(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"stck_prpr": "71500"}}
        )
        self.assertEqual(self.broker.get_current_price("005930.KS"), 71500.0)

    @patch("broker.kis_broker.requests.post")
    @patch("broker.kis_auth.requests.get")
    def test_place_order_buy_success(self, mock_get, mock_post):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "1000000"}]},
        )
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "0000123456"}}
        )
        result = self.broker.place_order("005930.KS", Signal.BUY, 1, 70000.0)
        self.assertTrue(result.filled)
        self.assertIn("0000123456", result.message)

        sent_headers = mock_post.call_args.kwargs["headers"]
        self.assertEqual(sent_headers["tr_id"], "VTTC0802U")  # demo buy tr_id

    @patch("broker.kis_broker.requests.post")
    def test_place_order_rejects_non_positive_quantity(self, mock_post):
        result = self.broker.place_order("005930.KS", Signal.BUY, 0, 70000.0)
        self.assertFalse(result.filled)
        mock_post.assert_not_called()

    @patch("broker.kis_broker.requests.post")
    def test_place_order_reports_api_failure(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "1", "msg1": "insufficient balance"}
        )
        result = self.broker.place_order("005930.KS", Signal.BUY, 1, 70000.0)
        self.assertFalse(result.filled)
        self.assertEqual(result.message, "insufficient balance")

    @patch("broker.kis_auth.time.sleep")
    @patch("broker.kis_auth.requests.get")
    def test_transient_500_on_get_is_retried_then_succeeds(self, mock_get, _mock_sleep):
        mock_get.side_effect = [
            MagicMock(status_code=500, text="server hiccup"),
            MagicMock(status_code=200, json=lambda: {"rt_cd": "0", "output": {"stck_prpr": "71500"}}),
        ]
        price = self.broker.get_current_price("005930.KS")
        self.assertEqual(price, 71500.0)
        self.assertEqual(mock_get.call_count, 2)

    @patch("broker.kis_broker.requests.post")
    def test_place_order_does_not_retry_ambiguous_500(self, mock_post):
        # A 5xx on order submission means we can't tell if the order was
        # actually accepted before the error - must surface as a failure
        # without retrying (retrying could submit a duplicate order).
        mock_post.return_value = MagicMock(status_code=500, text="server hiccup")
        result = self.broker.place_order("005930.KS", Signal.BUY, 1, 70000.0)
        self.assertFalse(result.filled)
        self.assertIn("order status unknown", result.message)
        self.assertEqual(mock_post.call_count, 1)


class TestKisBrokerCashLedger(unittest.TestCase):
    """Without seed_capital, get_cash_balance() must keep reading the real
    account (covered by TestKisBroker above, which never passes it) - these
    cover the opt-in local ledger used to scope position sizing to the
    configured seed instead of the real account's actual balance.
    """

    def setUp(self):
        self.env_patcher = _env_patch()
        self.env_patcher.start()
        self.token_patcher = patch("broker.kis_auth.KisSession.get_access_token", return_value="fake-token")
        self.token_patcher.start()
        self.broker = KisBroker(env="demo", watchlist=["005930.KS"], seed_capital=500_000.0)

    def tearDown(self):
        self.token_patcher.stop()
        self.env_patcher.stop()

    @patch("broker.kis_auth.requests.get")
    def test_seed_capital_ignores_real_account_balance(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "10000000"}]},
        )
        self.assertEqual(self.broker.get_cash_balance(), 500_000.0)
        mock_get.assert_not_called()  # ledger short-circuits the balance API call entirely

    @patch("broker.kis_broker.requests.post")
    def test_place_order_buy_debits_ledger(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "1"}}
        )
        self.broker.place_order("005930.KS", Signal.BUY, 2, 70000.0)
        self.assertEqual(self.broker.get_cash_balance(), 500_000.0 - 2 * 70000.0)

    @patch("broker.kis_auth.requests.get")
    @patch("broker.kis_broker.requests.post")
    def test_place_order_sell_credits_ledger(self, mock_post, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "2", "pchs_avg_pric": "70000"}],
                "output2": [{"dnca_tot_amt": "0"}],
            },
        )
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "2"}}
        )
        self.broker.place_order("005930.KS", Signal.SELL, 2, 75000.0)
        self.assertEqual(self.broker.get_cash_balance(), 500_000.0 + 2 * 75000.0)

    @patch("broker.kis_broker.requests.post")
    def test_failed_order_does_not_touch_ledger(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "1", "msg1": "insufficient balance"}
        )
        self.broker.place_order("005930.KS", Signal.BUY, 1, 70000.0)
        self.assertEqual(self.broker.get_cash_balance(), 500_000.0)

    def test_ledger_to_dict_restore_round_trip(self):
        snapshot = {"cash": 123456.0}
        self.broker.restore_ledger(snapshot)
        self.assertEqual(self.broker.get_cash_balance(), 123456.0)
        self.assertEqual(self.broker.ledger_to_dict(), snapshot)

    def test_restore_ledger_empty_state_is_noop(self):
        self.broker.restore_ledger({})
        self.assertEqual(self.broker.get_cash_balance(), 500_000.0)


if __name__ == "__main__":
    unittest.main()
