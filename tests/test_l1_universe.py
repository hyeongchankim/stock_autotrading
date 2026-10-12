"""screening/l1_universe.py + forward_log.record_l1_hits + the report's universe-found group."""
from __future__ import annotations

import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from screening.forward_log import record_candidates, record_l1_hits
from screening.forward_report import summarize, to_frame
from screening.l1_universe import scan_l1_hits
from utils.state_store import StateStore

DAY = date(2026, 10, 6)


def _bars(n: int = 70, hit: bool = True, last_day: str = "2026-10-06") -> pd.DataFrame:
    idx = pd.bdate_range(end=last_day, periods=n)
    close = np.linspace(100.0, 120.0, n)
    open_, high, vol = close.copy(), close * 1.005, np.full(n, 1000.0)
    if hit:
        open_[-1] = high[-2] * 1.03
        high[-1], close[-1] = open_[-1] * 1.01, open_[-1] * 1.005
        vol[-1] = 3000.0
    return pd.DataFrame({"open": open_, "high": high, "low": close * 0.99, "close": close, "volume": vol}, index=idx)


class _Feed:
    def __init__(self, bars: dict, fail=(), delay: float = 0.0):
        self.bars, self.fail, self.delay, self.calls = bars, set(fail), delay, 0

    def get_ohlcv(self, symbol, interval, lookback):
        self.calls += 1
        time.sleep(self.delay)
        if symbol in self.fail:
            raise RuntimeError("KIS down")
        return self.bars[symbol]


class TestScanL1Hits(unittest.TestCase):
    def test_returns_only_hits_with_a_row_and_counts_every_outcome(self):
        feed = _Feed({
            "HIT.KS": _bars(), "MISS.KS": _bars(hit=False), "STALE.KS": _bars(last_day="2026-10-05"),
            "SHORT.KS": _bars(n=30), "BAD.KS": _bars(),
        }, fail={"BAD.KS"})
        hits, stats = scan_l1_hits(feed, list(feed.bars), DAY)
        self.assertEqual(stats, {"hit": 1, "miss": 1, "skip": 2, "error": 1, "unvisited": 0})
        self.assertEqual([h["symbol"] for h in hits], ["HIT.KS"])
        row = hits[0]
        self.assertAlmostEqual(row["price"], float(_bars()["close"].iloc[-1]))
        self.assertEqual(row["volume"], 3000)
        self.assertAlmostEqual(row["trading_value"], row["price"] * 3000)
        self.assertGreater(row["change_pct"], 2.0)
        self.assertTrue(row["features"]["l1"])

    def test_the_deadline_stops_the_scan_and_reports_what_was_not_reached(self):
        feed = _Feed({f"{i}.KS": _bars() for i in range(12)}, delay=0.05)
        hits, stats = scan_l1_hits(feed, list(feed.bars), DAY, workers=1, max_seconds=0.15)
        self.assertGreater(stats["unvisited"], 0)
        self.assertEqual(sum(stats.values()), 12)
        self.assertLess(feed.calls, 12)


class TestRecordL1Hits(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self._tmp.name) / "log.json")

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _hit(symbol, price=100.0):
        return {"symbol": symbol, "price": price, "change_pct": 3.0, "volume": 3000, "trading_value": price * 3000,
                "features": {"l1": True, "vol_ratio": 3.0}}

    def test_adds_rows_tagged_as_universe_found_and_skips_already_logged_symbols(self):
        scan = pd.DataFrame({"symbol": ["A.KS"], "name": ["a"], "price": [100.0], "change_pct": [5.0], "volume": [1], "trading_value": [3e9]})
        record_candidates(self.store, DAY, scan, set(), now=datetime(2026, 10, 6, 15, 20))
        added = record_l1_hits(self.store, DAY, [self._hit("A.KS"), self._hit("B.KS")], seen={"A.KS"},
                               now=datetime(2026, 10, 6, 15, 25))
        self.assertEqual(added, 1)
        rows = {r["symbol"]: r for r in self.store.load()["records"]}
        self.assertEqual(set(rows), {"A.KS", "B.KS"})  # A.KS keeps its single candidate row
        self.assertEqual(rows["B.KS"]["source"], "l1_universe")
        self.assertIsNone(rows["B.KS"]["rank"])
        self.assertEqual(rows["B.KS"]["scan_tag"], {"l1": True, "vol_ratio": 3.0})
        self.assertNotIn("source", rows["A.KS"])

    def test_rerunning_replaces_only_its_own_rows_for_that_day(self):
        record_l1_hits(self.store, DAY, [self._hit("B.KS")], seen=set())
        record_l1_hits(self.store, date(2026, 10, 7), [self._hit("C.KS")], seen=set())
        record_l1_hits(self.store, DAY, [self._hit("D.KS")], seen=set())  # same day again: B replaced by D
        self.assertEqual(sorted((r["date"], r["symbol"]) for r in self.store.load()["records"]),
                         [("2026-10-06", "D.KS"), ("2026-10-07", "C.KS")])

    def test_the_fill_step_measures_universe_rows_like_any_other(self):
        from screening.forward_log import fill_outcomes
        record_l1_hits(self.store, DAY, [self._hit("HIT.KS", price=130.0)], seen=set(), now=datetime(2026, 10, 6, 15, 25))
        idx = pd.bdate_range(end="2026-10-07", periods=70)
        bars = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0}, index=idx)
        bars.iloc[-2, bars.columns.get_loc("close")] = 130.0   # signal day's close
        bars.iloc[-1, bars.columns.get_loc("open")] = 131.3    # next open: +1% over 130
        bars.iloc[-1, bars.columns.get_loc("close")] = 129.0
        fill_outcomes(self.store, _Feed({"HIT.KS": bars}), now=datetime(2026, 10, 8, 11, 0))
        row = self.store.load()["records"][0]
        self.assertEqual((row["close"], row["open_next"], row["close_next"]), (130.0, 131.3, 129.0))


class TestReportUniverseGroups(unittest.TestCase):
    def test_universe_found_rows_form_their_own_group_and_stay_out_of_all_logged(self):
        base = {"date": "2026-10-06", "close": 101.0, "open_next": 102.0, "close_next": 101.0}
        records = [
            {**base, "symbol": "A", "rank": 1, "qualified": True, "price_scan": 100.0, "scan_tag": {"l1": True}},
            {**base, "symbol": "B", "rank": 2, "qualified": True, "price_scan": 100.0, "scan_tag": {"l1": False}},
            {**base, "symbol": "U", "rank": None, "qualified": False, "price_scan": 100.0, "source": "l1_universe", "scan_tag": {"l1": True}},
        ]
        table = summarize(to_frame(records, 0.002)).set_index("group")
        self.assertEqual(table.loc["L1 @15:20", "n"], 2)  # A (candidate) + U (universe-found)
        self.assertEqual(table.loc["L1 @15:20, found by the universe scan", "n"], 1)
        self.assertEqual(table.loc["L1 @15:20, already a top-mover candidate", "n"], 1)
        self.assertEqual(table.loc["all logged", "n"], 2)  # the candidate population only


if __name__ == "__main__":
    unittest.main()
