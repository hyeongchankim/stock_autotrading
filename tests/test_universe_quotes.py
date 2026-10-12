"""screening/universe_quotes.py: batching, parsing, failure handling and the 1차 필터 + ranking."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from screening import universe_quotes as uq

DISC = {"min_price": 5000, "max_change_pct": 15.0, "min_trading_value": 3e9}


def _row(ticker, price=10_000, chg=1.0, value=5e9, vol=500_000):
    return {
        "inter_shrn_iscd": ticker, "inter_kor_isnm": f"name{ticker}", "inter2_prpr": str(price), "prdy_ctrt": str(chg),
        "acml_vol": str(vol), "acml_tr_pbmn": str(value), "inter2_oprc": str(price), "inter2_hgpr": str(price),
        "inter2_prdy_clpr": str(price),
    }


class _Session:
    """Stands in for KisSession: market_data_session() is itself."""
    base_url = "https://real.example"

    def market_data_session(self):
        return self

    def headers(self, tr_id):
        return {"tr_id": tr_id}


def _response(rows, rt_cd="0", msg="ok"):
    return MagicMock(json=lambda: {"rt_cd": rt_cd, "msg1": msg, "output": rows})


class TestFetchQuotes(unittest.TestCase):
    def test_batches_of_30_with_numbered_params_and_parses_every_field(self):
        tickers = [f"{i:06d}" for i in range(65)]  # 30 + 30 + 5
        seen = []

        def fake_get(url, headers, params):
            seen.append((url, headers, dict(params)))
            batch = [params[k] for k in sorted(params, key=lambda k: int(k.rsplit("_", 1)[1])) if k.startswith("FID_INPUT_ISCD_")]
            return _response([_row(t, price=20_000, chg=2.5, value=7e9, vol=123) for t in batch])

        with patch.object(uq, "get_with_retry", side_effect=fake_get):
            df = uq.fetch_quotes(_Session(), tickers)
        self.assertEqual(len(seen), 3)
        self.assertTrue(seen[0][0].endswith("/uapi/domestic-stock/v1/quotations/intstock-multprice"))
        self.assertEqual(seen[0][1]["tr_id"], "FHKST11300006")
        self.assertEqual(len(seen[0][2]), 60)   # 30 market codes + 30 tickers
        self.assertEqual(seen[0][2]["FID_INPUT_ISCD_1"], "000000")
        self.assertEqual(seen[0][2]["FID_COND_MRKT_DIV_CODE_30"], "J")
        self.assertEqual(len(seen[2][2]), 10)   # the last batch only has 5 symbols
        self.assertEqual(list(df.columns), uq.COLUMNS)
        self.assertEqual(len(df), 65)
        row = df.iloc[0]
        self.assertEqual((row["price"], row["change_pct"], row["volume"], row["trading_value"]), (20_000.0, 2.5, 123, 7e9))

    def test_a_failed_batch_raises_instead_of_returning_a_partial_universe(self):
        with patch.object(uq, "get_with_retry", return_value=_response([], rt_cd="1", msg="rate limited")):
            with self.assertRaises(RuntimeError):
                uq.fetch_quotes(_Session(), ["005930"])


class TestScreen(unittest.TestCase):
    UNIVERSE = {"000001.KS": "000001", "000002.KQ": "000002", "000003.KS": "000003", "000004.KS": "000004", "000005.KS": "000005"}

    def _quotes(self):
        return pd.DataFrame([
            {"ticker": "000001", "name": "a", "price": 10_000, "change_pct": 3.0, "volume": 1, "trading_value": 5e9, "open": 0, "high": 0, "prev_close": 0},
            {"ticker": "000002", "name": "b", "price": 20_000, "change_pct": 9.0, "volume": 1, "trading_value": 5e9, "open": 0, "high": 0, "prev_close": 0},
            {"ticker": "000003", "name": "c", "price": 4_000, "change_pct": 12.0, "volume": 1, "trading_value": 5e9, "open": 0, "high": 0, "prev_close": 0},   # price floor
            {"ticker": "000004", "name": "d", "price": 10_000, "change_pct": 20.0, "volume": 1, "trading_value": 5e9, "open": 0, "high": 0, "prev_close": 0},  # over +15%
            {"ticker": "000005", "name": "e", "price": 10_000, "change_pct": -2.0, "volume": 1, "trading_value": 1e9, "open": 0, "high": 0, "prev_close": 0},  # illiquid
            {"ticker": "999999", "name": "z", "price": 10_000, "change_pct": 5.0, "volume": 1, "trading_value": 5e9, "open": 0, "high": 0, "prev_close": 0},  # not in the universe
        ])

    def test_filters_like_the_discovery_first_pass_and_ranks_strongest_first(self):
        out = uq.screen_candidates(self._quotes(), self.UNIVERSE, **{"min_price": 5000, "max_change_pct": 15.0, "min_trading_value": 3e9})
        self.assertEqual(out["symbol"].tolist(), ["000002.KQ", "000001.KS"])  # b (+9) before a (+3)
        self.assertEqual(out["change_pct"].tolist(), [9.0, 3.0])

    def test_a_down_day_ranks_the_least_bad_first_not_an_empty_list(self):
        q = self._quotes().iloc[[0, 1]].copy()
        q["change_pct"] = [-4.0, -1.0]
        out = uq.screen_candidates(q, self.UNIVERSE, 5000, 15.0, 3e9)
        self.assertEqual(out["symbol"].tolist(), ["000002.KQ", "000001.KS"])

    def test_universe_candidates_rejects_a_quote_set_missing_too_many_symbols(self):
        universe = {f"{i:06d}.KS": f"{i:06d}" for i in range(100)}
        with patch.object(uq, "fetch_quotes", return_value=self._quotes()):  # 6 of 100
            with self.assertRaises(RuntimeError):
                uq.universe_candidates(_Session(), universe, DISC)

    def test_universe_candidates_end_to_end(self):
        quotes = self._quotes().iloc[:5]
        with patch.object(uq, "fetch_quotes", return_value=quotes):
            out = uq.universe_candidates(_Session(), self.UNIVERSE, DISC)
        self.assertEqual(out["symbol"].tolist(), ["000002.KQ", "000001.KS"])
        self.assertTrue(out["change_pct"].is_monotonic_decreasing)



class TestDiscoveryCheckpointUsesTheTopN(unittest.TestCase):
    def test_a_scan_checkpoint_records_only_the_strongest_n_not_every_passing_name(self):
        import tempfile
        from pathlib import Path

        import main as app
        from utils.state_store import StateStore

        symbols = [f"{i:06d}.KS" for i in range(40)]
        candidates = pd.DataFrame({
            "symbol": symbols, "ticker": [s.split(".")[0] for s in symbols], "name": symbols,
            "price": 10_000.0, "change_pct": [20.0 - i * 0.5 for i in range(40)],  # already strongest-first
            "volume": 1, "trading_value": 5e9, "open": 0.0, "high": 0.0, "prev_close": 0.0,
        })
        cfg = {"discovery": {**DISC, "enabled": True, "checkpoint_top_n": 7, "universe_cache_days": 7}, "broker": {"kis": {"env": "demo"}}}
        with tempfile.TemporaryDirectory() as tmp,              patch.object(app, "SCREENING_STATE_FILE", Path(tmp) / "screening.json"),              patch.object(app, "market_closed", return_value=False), patch.object(app, "fill_outcomes"),              patch.object(app, "KisDataFeed"), patch.object(app, "KisSession"),              patch.object(app, "get_universe", return_value={s: s for s in symbols}),              patch.object(app, "universe_candidates", return_value=candidates):
            app.run_discovery(cfg, "scan")
            recorded = StateStore(Path(tmp) / "screening.json").load()["checkpoints"]
        self.assertEqual(recorded, [symbols[:7]])

if __name__ == "__main__":
    unittest.main()
