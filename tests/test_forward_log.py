"""screening/forward_log.py + forward_report.py: recording, outcome filling
(only final values, once a day, holiday-proof) and the report arithmetic."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
