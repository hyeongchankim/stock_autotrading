"""data/market_calendar.py: closed only when the reference bar is positively older than today."""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from data.market_calendar import market_closed_today

TODAY = date(2026, 10, 6)


class _Feed:
    def __init__(self, last_bar: str | None = None, fail: bool = False):
        self.last_bar, self.fail, self.asked = last_bar, fail, []

    def get_ohlcv(self, symbol, interval, lookback):
        self.asked.append((symbol, interval, lookback))
        if self.fail:
            raise RuntimeError("KIS down")
        idx = pd.to_datetime(["2026-10-01", "2026-10-02", self.last_bar])
        return pd.DataFrame({"close": [1.0, 1.0, 1.0]}, index=idx)


class TestMarketClosedToday(unittest.TestCase):
    def test_today_has_a_bar_so_the_market_is_open(self):
        self.assertFalse(market_closed_today(_Feed("2026-10-06"), today=TODAY))

    def test_latest_bar_older_than_today_means_closed(self):
        # Monday 2026-10-05 was a holiday: the newest bar stays Friday's
        self.assertTrue(market_closed_today(_Feed("2026-10-02"), today=date(2026, 10, 5)))

    def test_any_data_problem_means_carry_on_not_closed(self):
        self.assertFalse(market_closed_today(_Feed(fail=True), today=TODAY))

    def test_asks_for_the_reference_stock_daily_bars(self):
        feed = _Feed("2026-10-06")
        market_closed_today(feed, today=TODAY)
        self.assertEqual(feed.asked, [("005930.KS", "1d", 5)])


class TestMainGuard(unittest.TestCase):
    def test_only_the_kis_provider_is_checked(self):
        import main as app
        with patch.object(app, "KisDataFeed", side_effect=AssertionError("no feed expected")):
            self.assertFalse(app.market_closed({"broker": {"provider": "mock"}}))

    def test_kis_provider_uses_the_calendar_result(self):
        import main as app
        cfg = {"broker": {"provider": "kis", "kis": {"env": "demo"}}}
        for closed in (True, False):
            with patch.object(app, "KisDataFeed"), patch.object(app, "market_closed_today", return_value=closed):
                self.assertEqual(app.market_closed(cfg), closed)

    def test_each_run_function_stops_before_doing_any_work_on_a_holiday(self):
        import main as app
        cfg = {"broker": {"provider": "kis"}, "macd_sleeve": {"enabled": True}, "discovery": {"enabled": True}}
        with patch.object(app, "market_closed", return_value=True),              patch.object(app, "build_broker", side_effect=AssertionError("must not trade")),              patch.object(app, "fill_outcomes"), patch.object(app, "get_universe", side_effect=AssertionError("must not scan")):
            app.run_paper({**cfg, "seed_capital": 1})
            app.run_macd_sleeve(cfg)
            app.run_discovery(cfg, "final")


if __name__ == "__main__":
    unittest.main()
