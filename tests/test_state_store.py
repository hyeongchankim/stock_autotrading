"""Sanity checks for the JSON state persistence helper.

Run with: pytest
(or: python -m unittest tests.test_state_store)
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from utils.state_store import StateStore


class TestStateStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "nested" / "state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_missing_file_returns_empty_dict(self):
        store = StateStore(self.path)
        self.assertEqual(store.load(), {})

    def test_save_then_load_round_trips(self):
        store = StateStore(self.path)
        state = {"risk_manager": {"date": "2026-07-15", "daily_realized_pnl": -1234.5}}
        store.save(state)
        self.assertEqual(StateStore(self.path).load(), state)

    def test_corrupt_file_returns_empty_dict(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not valid json", encoding="utf-8")
        store = StateStore(self.path)
        self.assertEqual(store.load(), {})


if __name__ == "__main__":
    unittest.main()
