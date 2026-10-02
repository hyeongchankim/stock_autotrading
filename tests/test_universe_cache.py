"""screening/discover.get_universe: a fresh cache means no KRX call at all, a
stale one is refreshed, and a failed refresh (KRX outage / IP ban) falls back
to the stale cache instead of stopping the discovery sleeve."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from screening import discover


def _universe(n: int = 300, tag: str = "x") -> dict[str, str]:
    return {f"{i:06d}.KS": f"{tag}{i}" for i in range(n)}


class TestUniverseCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "universe_cache.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_cache(self, age_days: int, universe: dict[str, str] | None = None):
        self.path.write_text(json.dumps({
            "fetched_on": (date.today() - timedelta(days=age_days)).isoformat(),
            "universe": universe if universe is not None else _universe(tag="cached"),
        }), encoding="utf-8")

    def test_fresh_cache_never_touches_krx(self):
        self._write_cache(age_days=2)
        with patch.object(discover, "_fetch_universe", side_effect=AssertionError("KRX was called")):
            result = discover.get_universe(max_age_days=7, cache_path=self.path)
        self.assertEqual(result, _universe(tag="cached"))

    def test_stale_cache_is_refreshed_and_rewritten(self):
        self._write_cache(age_days=10)
        with patch.object(discover, "_fetch_universe", return_value=_universe(tag="fresh")) as fetch:
            result = discover.get_universe(max_age_days=7, cache_path=self.path)
        fetch.assert_called_once()
        self.assertEqual(result, _universe(tag="fresh"))
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["fetched_on"], date.today().isoformat())
        self.assertEqual(saved["universe"], _universe(tag="fresh"))

    def test_refresh_failure_falls_back_to_the_stale_cache_and_keeps_it(self):
        self._write_cache(age_days=30)
        before = self.path.read_text(encoding="utf-8")
        with patch.object(discover, "_fetch_universe", side_effect=RuntimeError("KRX IP blocked")):
            result = discover.get_universe(max_age_days=7, cache_path=self.path)
        self.assertEqual(result, _universe(tag="cached"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)  # not overwritten

    def test_no_cache_and_krx_down_raises(self):
        with patch.object(discover, "_fetch_universe", side_effect=RuntimeError("KRX IP blocked")):
            with self.assertRaises(RuntimeError):
                discover.get_universe(max_age_days=7, cache_path=self.path)

    def test_corrupt_or_tiny_cache_counts_as_missing(self):
        self.path.write_text("{not json", encoding="utf-8")
        with patch.object(discover, "_fetch_universe", return_value=_universe(tag="fresh")) as fetch:
            self.assertEqual(discover.get_universe(cache_path=self.path), _universe(tag="fresh"))
        fetch.assert_called_once()
        self._write_cache(age_days=0, universe={"000001.KS": "x"})  # 1 entry: a bad cache, not a universe
        with patch.object(discover, "_fetch_universe", return_value=_universe(tag="fresh2")) as fetch:
            self.assertEqual(discover.get_universe(cache_path=self.path), _universe(tag="fresh2"))
        fetch.assert_called_once()

    def test_fetch_rejects_a_suspiciously_small_krx_answer(self):
        class _Stock:
            @staticmethod
            def get_index_portfolio_deposit_file(code):
                return ["005930"] if code == "1028" else []

        with patch.dict("sys.modules", {"pykrx": type("M", (), {"stock": _Stock})}):
            with self.assertRaises(ValueError):
                discover._fetch_universe()


if __name__ == "__main__":
    unittest.main()
