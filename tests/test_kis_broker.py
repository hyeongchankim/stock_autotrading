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
from broker.kis_broker import KisBroker, OrderFill, _to_kis_ticker
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

    def test_market_data_session_is_self_for_real_env(self):
        with _env_patch():
            session = KisSession(env="real")
            self.assertIs(session.market_data_session(), session)

    def test_market_data_session_is_a_real_session_for_demo_env(self):
        with _env_patch():
            session = KisSession(env="demo")
            md_session = session.market_data_session()
            self.assertEqual(md_session.env, "real")
            self.assertEqual(md_session.base_url, session.market_data_session().base_url)
            self.assertNotEqual(md_session.base_url, session.base_url)

    def test_market_data_session_is_cached(self):
        with _env_patch():
            session = KisSession(env="demo")
            self.assertIs(session.market_data_session(), session.market_data_session())

    def test_market_data_session_requires_real_credentials(self):
        with _env_patch(KIS_APP_KEY="", KIS_APP_SECRET=""):
            session = KisSession(env="demo")  # demo credentials alone are fine to construct
            with self.assertRaises(KisCredentialsError):
                session.market_data_session()  # but real ones are needed for market data


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

    @patch("broker.kis_auth.requests.get")
    def test_get_current_price_uses_real_domain_even_for_demo_broker(self, mock_get):
        # self.broker is env="demo" (see setUp) - quotes must still go
        # through the real domain (market_data_session()).
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"stck_prpr": "71500"}}
        )
        self.broker.get_current_price("005930.KS")
        sent_url = mock_get.call_args.args[0]
        self.assertIn("openapi.koreainvestment.com", sent_url)
        self.assertNotIn("openapivts", sent_url)

    @patch("broker.kis_broker.requests.post")
    @patch("broker.kis_auth.requests.get")
    def test_place_order_buy_success(self, mock_get, mock_post):
        # pre-order balance check sees nothing held, post-order check (inside
        # _actual_bought_qty) sees 1 - a full fill, resolved on the first poll.
        pre_order = MagicMock(
            status_code=200,
            json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "1000000"}]},
        )
        post_order = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "1", "pchs_avg_pric": "70000"}],
                "output2": [{"dnca_tot_amt": "1000000"}],
            },
        )
        mock_get.side_effect = [pre_order, post_order]
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "0000123456"}}
        )
        result = self.broker.place_order("005930.KS", Signal.BUY, 1, 70000.0)
        self.assertTrue(result.filled)
        self.assertIn("0000123456", result.message)

        sent_headers = mock_post.call_args.kwargs["headers"]
        self.assertEqual(sent_headers["tr_id"], "VTTC0802U")  # demo buy tr_id

    @patch("broker.kis_broker.requests.post")
    @patch("broker.kis_auth.requests.get")
    def test_place_order_rounds_price_to_valid_krx_tick(self, mock_get, mock_post):
        pre_order = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "0"}]}
        )
        post_order = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "1", "pchs_avg_pric": "70000"}],
                "output2": [{"dnca_tot_amt": "0"}],
            },
        )
        mock_get.side_effect = [pre_order, post_order]
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "1"}}
        )
        # 70,034 is not a valid 100-won-tier price - must round to 70,000
        # before it's sent to KIS, or the order would be rejected off-tick.
        result = self.broker.place_order("005930.KS", Signal.BUY, 1, 70034.0)
        self.assertEqual(result.price, 70000.0)

        sent_body = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_body["ORD_UNPR"], "70000")

    @patch("broker.kis_broker.requests.post")
    def test_place_order_rejects_non_positive_quantity(self, mock_post):
        result = self.broker.place_order("005930.KS", Signal.BUY, 0, 70000.0)
        self.assertFalse(result.filled)
        mock_post.assert_not_called()

    @patch("broker.kis_broker.requests.post")
    @patch("broker.kis_auth.requests.get")
    def test_place_order_reports_api_failure(self, mock_get, mock_post):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "0"}]}
        )
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
    @patch("broker.kis_auth.requests.get")
    def test_place_order_does_not_retry_ambiguous_500(self, mock_get, mock_post):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "0"}]}
        )
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
    @patch("broker.kis_auth.requests.get")
    def test_place_order_buy_debits_ledger(self, mock_get, mock_post):
        pre_order = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "0"}]}
        )
        post_order = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "2", "pchs_avg_pric": "70000"}],
                "output2": [{"dnca_tot_amt": "0"}],
            },
        )
        mock_get.side_effect = [pre_order, post_order]
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "1"}}
        )
        self.broker.place_order("005930.KS", Signal.BUY, 2, 70000.0)
        self.assertEqual(self.broker.get_cash_balance(), 500_000.0 - 2 * 70000.0)

    @patch("broker.kis_auth.requests.get")
    @patch("broker.kis_broker.requests.post")
    def test_place_order_sell_credits_ledger(self, mock_post, mock_get):
        # pre-order balance check sees 2 held, post-order check (inside
        # _actual_sold_qty) sees 0 - a full fill, resolved on the first poll.
        pre_order = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "2", "pchs_avg_pric": "70000"}],
                "output2": [{"dnca_tot_amt": "0"}],
            },
        )
        post_order = MagicMock(
            status_code=200,
            json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "0"}]},
        )
        mock_get.side_effect = [pre_order, post_order]
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "2"}}
        )
        self.broker.place_order("005930.KS", Signal.SELL, 2, 75000.0)
        self.assertEqual(self.broker.get_cash_balance(), 500_000.0 + 2 * 75000.0)

    @patch("broker.kis_broker.requests.post")
    @patch("broker.kis_auth.requests.get")
    def test_failed_order_does_not_touch_ledger(self, mock_get, mock_post):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "0"}]}
        )
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


class TestKisBrokerCosts(unittest.TestCase):
    """commission_pct/sell_tax_pct should shrink the ledger the same way
    MockBroker's Portfolio applies them - see KisBroker._effective_price.
    """

    def setUp(self):
        self.env_patcher = _env_patch()
        self.env_patcher.start()
        self.token_patcher = patch("broker.kis_auth.KisSession.get_access_token", return_value="fake-token")
        self.token_patcher.start()
        self.broker = KisBroker(
            env="demo", watchlist=["005930.KS"], seed_capital=500_000.0,
            commission_pct=1.0, sell_tax_pct=2.0,
        )

    def tearDown(self):
        self.token_patcher.stop()
        self.env_patcher.stop()

    @patch("broker.kis_broker.requests.post")
    @patch("broker.kis_auth.requests.get")
    def test_buy_debits_ledger_with_commission(self, mock_get, mock_post):
        pre_order = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "0"}]}
        )
        post_order = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "1", "pchs_avg_pric": "100"}],
                "output2": [{"dnca_tot_amt": "0"}],
            },
        )
        mock_get.side_effect = [pre_order, post_order]
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "1"}}
        )
        self.broker.place_order("005930.KS", Signal.BUY, 1, 100.0)
        # effective buy price = 100 * 1.01 = 101
        self.assertAlmostEqual(self.broker.get_cash_balance(), 500_000.0 - 101.0)

    @patch("broker.kis_auth.requests.get")
    @patch("broker.kis_broker.requests.post")
    def test_sell_credits_ledger_net_of_commission_and_tax(self, mock_post, mock_get):
        pre_order = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "1", "pchs_avg_pric": "100"}],
                "output2": [{"dnca_tot_amt": "0"}],
            },
        )
        post_order = MagicMock(
            status_code=200,
            json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "0"}]},
        )
        mock_get.side_effect = [pre_order, post_order]
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "2"}}
        )
        result = self.broker.place_order("005930.KS", Signal.SELL, 1, 200.0)
        # effective sell price = 200 * (1 - 0.01 - 0.02) = 194
        self.assertAlmostEqual(self.broker.get_cash_balance(), 500_000.0 + 194.0)
        self.assertAlmostEqual(result.realized_pnl, 194.0 - 100.0)

    @patch("broker.kis_broker.requests.post")
    @patch("broker.kis_auth.requests.get")
    def test_order_sent_to_kis_uses_raw_fill_price_not_effective_price(self, mock_get, mock_post):
        pre_order = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "0"}]}
        )
        post_order = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "1", "pchs_avg_pric": "100"}],
                "output2": [{"dnca_tot_amt": "0"}],
            },
        )
        mock_get.side_effect = [pre_order, post_order]
        # our commission/tax model is local-only bookkeeping - KIS must
        # still be sent the actual fill price, not a cost-adjusted one.
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "1"}}
        )
        self.broker.place_order("005930.KS", Signal.BUY, 1, 100.0)
        sent_body = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_body["ORD_UNPR"], "100")


class TestKisBrokerDailyFills(unittest.TestCase):
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
    def test_parses_partial_fill(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [
                    {
                        "odno": "0000123456",
                        "pdno": "005930",
                        "ord_qty": "10",
                        "tot_ccld_qty": "3",
                        "rmn_qty": "7",
                        "avg_prvs": "71000",
                    }
                ],
            },
        )
        fills = self.broker.get_daily_fills()
        self.assertEqual(len(fills), 1)
        fill = fills[0]
        self.assertEqual(fill, OrderFill(
            order_no="0000123456", symbol="005930.KS",
            ordered_qty=10, filled_qty=3, pending_qty=7, avg_fill_price=71000.0,
        ))

    @patch("broker.kis_auth.requests.get")
    def test_uses_demo_tr_id(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"rt_cd": "0", "output1": []})
        self.broker.get_daily_fills()
        sent_headers = mock_get.call_args.kwargs["headers"]
        self.assertEqual(sent_headers["tr_id"], "VTTC0081R")

    @patch("broker.kis_auth.requests.get")
    def test_filters_by_order_no(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"rt_cd": "0", "output1": []})
        self.broker.get_daily_fills(order_no="0000123456")
        sent_params = mock_get.call_args.kwargs["params"]
        self.assertEqual(sent_params["ODNO"], "0000123456")

    @patch("broker.kis_auth.requests.get")
    def test_raises_on_api_failure(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "1", "msg1": "account not found"}
        )
        with self.assertRaises(RuntimeError):
            self.broker.get_daily_fills()


class TestKisBrokerPartialFill(unittest.TestCase):
    """place_order() previously computed realized_pnl and the local ledger
    off the requested SELL quantity regardless of how much actually
    filled - a partially-filled stop-loss SELL would have recorded a
    too-large realized loss, corrupting the daily_max_loss_pct circuit
    breaker's accuracy. _actual_sold_qty() fixes this by diffing
    get_positions() before/after the order.
    """

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
    @patch("broker.kis_broker.requests.post")
    def test_partial_fill_uses_actual_sold_qty_not_requested(self, mock_post, mock_get):
        # requested 10, but only 3 actually sold - held quantity only drops 10->7
        pre_order = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "10", "pchs_avg_pric": "100"}],
                "output2": [{"dnca_tot_amt": "0"}],
            },
        )
        post_order = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "7", "pchs_avg_pric": "100"}],
                "output2": [{"dnca_tot_amt": "0"}],
            },
        )
        mock_get.side_effect = [pre_order, post_order]
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "1"}}
        )
        result = self.broker.place_order("005930.KS", Signal.SELL, 10, 150.0)
        self.assertEqual(result.quantity, 3)  # actual fill, not the requested 10
        self.assertAlmostEqual(result.realized_pnl, (150.0 - 100.0) * 3)  # not * 10

    @patch("broker.kis_broker.time.sleep")
    @patch("broker.kis_auth.requests.get")
    @patch("broker.kis_broker.requests.post")
    def test_falls_back_to_requested_qty_if_position_never_updates(self, mock_post, mock_get, _mock_sleep):
        # get_positions() keeps reporting the pre-order quantity on every
        # poll (simulates the account never reflecting the fill in time) -
        # must fall back to assuming the requested quantity filled, not 0.
        stale = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "5", "pchs_avg_pric": "100"}],
                "output2": [{"dnca_tot_amt": "0"}],
            },
        )
        mock_get.return_value = stale
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "1"}}
        )
        result = self.broker.place_order("005930.KS", Signal.SELL, 5, 150.0)
        self.assertEqual(result.quantity, 5)
        self.assertAlmostEqual(result.realized_pnl, (150.0 - 100.0) * 5)

    @patch("broker.kis_broker.time.sleep")
    @patch("broker.kis_auth.requests.get")
    @patch("broker.kis_broker.requests.post")
    def test_buy_reports_unfilled_instead_of_assuming_full_fill(self, mock_post, mock_get, _mock_sleep):
        # get_positions() keeps reporting no holding on every poll (a
        # 지정가/limit BUY that never actually matched) - unlike the SELL
        # fallback above, this must NOT assume the requested quantity
        # filled: that would silently debit the local cash ledger for
        # shares never purchased, with nothing to reconcile it back later
        # (see _actual_bought_qty's docstring). Confirmed via a manual
        # forced-entry test where a demo BUY order sat unfilled for 10+
        # minutes while place_order() had already deducted the ledger.
        never_filled = MagicMock(
            status_code=200,
            json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "0"}]},
        )
        mock_get.return_value = never_filled
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "1"}}
        )
        result = self.broker.place_order("005930.KS", Signal.BUY, 2, 100.0)
        self.assertFalse(result.filled)
        self.assertEqual(result.quantity, 0)
        self.assertIn("not filled yet", result.message)

    @patch("broker.kis_auth.requests.get")
    @patch("broker.kis_broker.requests.post")
    def test_diff_larger_than_requested_is_capped(self, mock_post, mock_get):
        # defensive: even if the diff somehow exceeds what was requested
        # (e.g. a concurrent manual trade), never report more than requested.
        pre_order = MagicMock(
            status_code=200,
            json=lambda: {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "10", "pchs_avg_pric": "100"}],
                "output2": [{"dnca_tot_amt": "0"}],
            },
        )
        post_order = MagicMock(
            status_code=200,
            json=lambda: {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "0"}]},
        )
        mock_get.side_effect = [pre_order, post_order]
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "1"}}
        )
        result = self.broker.place_order("005930.KS", Signal.SELL, 3, 150.0)
        self.assertEqual(result.quantity, 3)  # capped at requested, not the full 10 sold


if __name__ == "__main__":
    unittest.main()
