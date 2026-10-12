"""screening/forward_log.py + forward_report.py: recording, outcome filling
(only final values, once a day, holiday-proof) and the report arithmetic."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from screening.forward_log import fill_outcomes, record_candidates
from screening.forward_report import summarize, to_frame
from utils.state_store import StateStore

FRI, MON, TUE = date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)


def _scan() -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": ["B.KS", "A.KS", "C.KQ"], "name": ["b", "a", "c"], "price": [10_000.0, 20_000.0, 5_000.0],
        "change_pct": [3.0, 9.0, 1.0], "volume": [1_000, 2_000, 3_000], "trading_value": [4e9, 5e9, 6e9],
    })


class _Feed:
    """Bars for A.KS around a weekend: Fri 10-02 then Mon 10-05."""

    def __init__(self, fail: bool = False):
        self.fail, self.calls = fail, 0

    def get_ohlcv(self, symbol, interval, lookback):
        self.calls += 1
        if self.fail:
            raise RuntimeError("KIS down")
        idx = pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-05"])
        return pd.DataFrame({"open": [1, 100, 101.0], "close": [1, 102, 100.5]}, index=idx)


class TestRecord(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self._tmp.name) / "log.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_ranks_by_change_flags_qualified_and_replaces_same_day(self):
        record_candidates(self.store, FRI, _scan(), {"B.KS"}, now=datetime(2026, 10, 2, 15, 20))
        record_candidates(self.store, MON, _scan(), set(), now=datetime(2026, 10, 5, 15, 20))
        n = record_candidates(self.store, FRI, _scan(), {"B.KS"}, now=datetime(2026, 10, 2, 15, 21))  # re-run
        rows = self.store.load()["records"]
        self.assertEqual(n, 3)
        self.assertEqual(len(rows), 6)  # re-run replaced Friday's 3 rows, Monday's kept
        fri = [r for r in rows if r["date"] == FRI.isoformat()]
        self.assertEqual([r["symbol"] for r in fri], ["A.KS", "B.KS", "C.KQ"])  # 9% > 3% > 1%
        self.assertEqual([r["rank"] for r in fri], [1, 2, 3])
        self.assertEqual({r["symbol"]: r["qualified"] for r in fri}, {"A.KS": False, "B.KS": True, "C.KQ": False})
        self.assertEqual(fri[0]["price_scan"], 20_000.0)


class TestFill(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self._tmp.name) / "log.json")
        scan = _scan().iloc[[1]]  # just A.KS
        record_candidates(self.store, FRI, scan, set(), now=datetime(2026, 10, 2, 15, 20))

    def tearDown(self):
        self._tmp.cleanup()

    def _row(self):
        return self.store.load()["records"][0]

    def test_same_day_provisional_close_is_not_filled(self):
        n = fill_outcomes(self.store, _Feed(), now=datetime(2026, 10, 2, 15, 25))  # closing auction not done
        self.assertEqual(n, 0)
        self.assertNotIn("close", self._row())

    def test_weekend_gap_next_trading_day_is_monday_and_values_are_final_only(self):
        # Monday 11:00: Friday close and Monday OPEN are final, Monday close is not
        n = fill_outcomes(self.store, _Feed(), now=datetime(2026, 10, 5, 11, 0))
        row = self._row()
        self.assertEqual(n, 2)
        self.assertEqual((row["close"], row["open_next"], row["next_date"]), (102.0, 101.0, "2026-10-05"))
        self.assertNotIn("close_next", row)
        # once per day: a second call the same day does nothing even after the close
        self.assertEqual(fill_outcomes(self.store, _Feed(), now=datetime(2026, 10, 5, 16, 0)), 0)
        # next day completes it
        self.assertEqual(fill_outcomes(self.store, _Feed(), now=datetime(2026, 10, 6, 11, 0)), 1)
        self.assertEqual(self._row()["close_next"], 100.5)

    def test_feed_failure_never_raises_and_is_retried_the_same_day(self):
        self.assertEqual(fill_outcomes(self.store, _Feed(fail=True), now=datetime(2026, 10, 6, 11, 0)), 0)
        self.assertNotIn("last_fill", self.store.load())  # not marked done -> a later run retries
        self.assertEqual(fill_outcomes(self.store, _Feed(), now=datetime(2026, 10, 6, 11, 5)), 3)

    def test_complete_rows_are_not_refetched(self):
        fill_outcomes(self.store, _Feed(), now=datetime(2026, 10, 6, 11, 0))
        feed = _Feed()
        fill_outcomes(self.store, feed, now=datetime(2026, 10, 7, 11, 0))
        self.assertEqual(feed.calls, 0)


class TestReport(unittest.TestCase):
    def test_returns_and_costs(self):
        records = [{
            "date": "2026-10-02", "symbol": "A.KS", "rank": 1, "qualified": True, "price_scan": 100.0,
            "close": 102.0, "open_next": 101.0, "close_next": 100.5,
        }]
        df = to_frame(records, round_trip_cost=0.002)
        row = df.iloc[0]
        self.assertAlmostEqual(row["scan->close"], 2.0)
        self.assertAlmostEqual(row["scan->open+1"], (0.01 - 0.002) * 100)  # +1% gap minus costs
        self.assertAlmostEqual(row["open+1->close+1"], (100.5 / 101 - 1) * 100)
        self.assertAlmostEqual(row["scan->close+1"], (0.005 - 0.002) * 100)
        table = summarize(df)
        self.assertEqual(set(table["group"]), {"top-3 by change%", "top-5 by change%", "top-10 by change%",
                                                "qualified (2+ checkpoints)", "all logged"})
        self.assertTrue((table["n"] == 1).all())



def _bars(n: int = 70, gap_on_last: bool = True, surge: bool = True) -> pd.DataFrame:
    """n daily bars ending 2026-10-06; a steady uptrend, and (optionally) an L1 setup on the last day."""
    idx = pd.bdate_range(end="2026-10-06", periods=n)
    close = np.linspace(100.0, 120.0, n)
    open_, high, vol = close.copy(), close * 1.005, np.full(n, 1000.0)
    if gap_on_last:
        open_[-1] = high[-2] * 1.03          # opens above yesterday's high, ~+3% over the close
        high[-1], close[-1] = open_[-1] * 1.01, open_[-1] * 1.005
    vol[-1] = 3000.0 if surge else 1000.0    # 3x the prior weekly mean, or not
    return pd.DataFrame({"open": open_, "high": high, "low": close * 0.99, "close": close, "volume": vol}, index=idx)


class _TagFeed:
    def __init__(self, bars: dict, fail: set | None = None):
        self.bars, self.fail = bars, fail or set()

    def get_ohlcv(self, symbol, interval, lookback):
        if symbol in self.fail:
            raise RuntimeError("KIS down")
        return self.bars[symbol]


class TestL1Tag(unittest.TestCase):
    def test_features_for_a_textbook_setup_and_for_each_missing_condition(self):
        from screening.gap_tag import l1_features
        day = pd.Timestamp("2026-10-06")
        f = l1_features(_bars(), day)
        self.assertTrue(f["l1"] and f["over_prev_high"] and f["above_trend"])
        self.assertAlmostEqual(f["vol_ratio"], 3.0)
        self.assertFalse(l1_features(_bars(surge=False), day)["l1"])          # no volume surge
        self.assertFalse(l1_features(_bars(gap_on_last=False), day)["l1"])    # no gap
        self.assertIsNone(l1_features(_bars(n=40), day))                      # too little history for SMA60
        self.assertIsNone(l1_features(_bars(), pd.Timestamp("2026-10-07")))  # no bar for that day

    def test_features_match_the_backtest_layer_one_on_random_data(self):
        from backtest.gap_breakout import Rule, layers
        from backtest.github_strategies import build_panels
        from screening.gap_tag import l1_features
        rng = np.random.default_rng(5)
        t, idx = 200, pd.bdate_range("2023-01-02", periods=200)
        data = {}
        for k in range(10):
            close = 20_000 * np.exp(np.cumsum(rng.normal(0.001, 0.025, t)))
            open_ = close * (1 + rng.normal(0.004, 0.03, t))
            data[f"{k:06d}.KS"] = pd.DataFrame({
                "open": open_, "high": np.maximum(open_, close) * (1 + rng.random(t) * 0.01),
                "low": np.minimum(open_, close) * 0.99, "close": close,
                "volume": rng.integers(1_000_000, 3_000_000, t) * (1 + 3 * (rng.random(t) < 0.12))}, index=idx)
        backtest = list(layers(build_panels(data), Rule()).values())[0]  # L1
        fired = compared = 0
        for symbol, df in data.items():
            for i in range(61, t):
                f = l1_features(df.iloc[: i + 1], df.index[i])
                self.assertEqual(f["l1"], bool(backtest.loc[df.index[i], symbol]), f"{symbol} bar {i}")
                compared += 1
                fired += f["l1"]
        self.assertGreater(fired, 0)  # not vacuous
        self.assertGreater(compared, 1000)

    def test_scan_tags_only_symbols_whose_newest_bar_is_today_and_never_raises(self):
        from screening.gap_tag import tag_scan
        feed = _TagFeed({"A": _bars(), "B": _bars().iloc[:-1], "C": _bars()}, fail={"C"})  # B: stale bar, C: feed error
        tags = tag_scan(feed, ["A", "B", "C"], date(2026, 10, 6))
        self.assertEqual(list(tags), ["A"])
        self.assertTrue(tags["A"]["l1"])

    def test_record_candidates_stores_the_scan_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "log.json")
            record_candidates(store, FRI, _scan(), set(), now=datetime(2026, 10, 2, 15, 20),
                              tags={"A.KS": {"l1": True, "vol_ratio": 3.0}})
            rows = {r["symbol"]: r for r in store.load()["records"]}
            self.assertEqual(rows["A.KS"]["scan_tag"], {"l1": True, "vol_ratio": 3.0})
            self.assertNotIn("scan_tag", rows["B.KS"])

    def test_fill_adds_the_eod_tag_once_and_only_for_final_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "log.json")
            scan = _scan().iloc[[1]].copy()   # A.KS only
            record_candidates(store, date(2026, 10, 6), scan, set(), now=datetime(2026, 10, 6, 15, 20))
            feed = _TagFeed({"A.KS": _bars()})
            fill_outcomes(store, feed, now=datetime(2026, 10, 6, 15, 25))      # the signal day's close isn't final yet
            self.assertNotIn("eod_tag", store.load()["records"][0])
            fill_outcomes(store, feed, now=datetime(2026, 10, 7, 11, 0))
            row = store.load()["records"][0]
            self.assertTrue(row["eod_tag"]["l1"])
            self.assertEqual(row["eod_tag"]["vol_ratio"], 3.0)


class TestReportL1Groups(unittest.TestCase):
    def test_groups_split_by_the_tag_and_untagged_rows_are_excluded(self):
        base = {"price_scan": 100.0, "close": 101.0, "open_next": 102.0, "close_next": 101.0, "qualified": True}
        records = [
            {**base, "date": "2026-10-06", "symbol": "A", "rank": 1, "scan_tag": {"l1": True}, "eod_tag": {"l1": True}},
            {**base, "date": "2026-10-06", "symbol": "B", "rank": 2, "open_next": 99.0, "scan_tag": {"l1": False}, "eod_tag": {"l1": True}},
            {**base, "date": "2026-10-06", "symbol": "C", "rank": 3, "eod_tag": {"l1": None}},   # unjudgeable, no scan tag
        ]
        table = summarize(to_frame(records, 0.002)).set_index("group")
        self.assertEqual(table.loc["L1 @15:20", "n"], 1)
        self.assertEqual(table.loc["not L1 @15:20", "n"], 1)
        self.assertEqual(table.loc["L1 by close", "n"], 2)
        self.assertNotIn("not L1 by close", table.index)
        self.assertGreater(table.loc["L1 @15:20", "scan->open+1"], table.loc["not L1 @15:20", "scan->open+1"])

if __name__ == "__main__":
    unittest.main()
