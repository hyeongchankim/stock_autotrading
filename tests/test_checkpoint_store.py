"""Sanity checks for the discovery-screening checkpoint persistence.

Run with: pytest
(or: python -m unittest tests.test_checkpoint_store)
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from screening.checkpoint_store import persistent_candidates, record_checkpoint
from utils.state_store import StateStore


class TestCheckpointStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self._tmp.name) / "screening_state.json")
        self.today = date(2026, 9, 30)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_checkpoints_today_returns_empty(self):
        self.assertEqual(persistent_candidates(self.store, self.today), set())

    def test_symbol_seen_in_two_checkpoints_persists(self):
        record_checkpoint(self.store, self.today, ["005930.KS", "000660.KS"])
        record_checkpoint(self.store, self.today, ["005930.KS", "035720.KS"])
        self.assertEqual(persistent_candidates(self.store, self.today), {"005930.KS"})

    def test_symbol_seen_once_does_not_persist(self):
        record_checkpoint(self.store, self.today, ["005930.KS"])
        self.assertEqual(persistent_candidates(self.store, self.today), set())

    def test_min_checkpoints_threshold_is_configurable(self):
        record_checkpoint(self.store, self.today, ["005930.KS"])
        record_checkpoint(self.store, self.today, ["005930.KS"])
        self.assertEqual(persistent_candidates(self.store, self.today, min_checkpoints=3), set())
        self.assertEqual(persistent_candidates(self.store, self.today, min_checkpoints=2), {"005930.KS"})

    def test_new_day_resets_checkpoints(self):
        yesterday = date(2026, 9, 29)
        record_checkpoint(self.store, yesterday, ["005930.KS"])
        record_checkpoint(self.store, yesterday, ["005930.KS"])
        record_checkpoint(self.store, self.today, ["000660.KS"])
        # yesterday's persistent candidate must not leak into today's count
        self.assertEqual(persistent_candidates(self.store, self.today), set())


if __name__ == "__main__":
    unittest.main()
