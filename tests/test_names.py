import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from utils import names


class NamesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.patches = [patch.object(names, "NAMES_FILE", Path(self.tmp.name) / "n.json"), patch.object(names, "_names", None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_annotates_known_symbols_only_and_not_twice(self):
        names.remember({"005930": "삼성전자", "058470.KQ": "리노공업"})
        out = names.annotate("entry 005930.KS qty=3, 058470.KQ, 999999.KS, price 70000.0")
        self.assertEqual(out, "entry 삼성전자(005930.KS) qty=3, 리노공업(058470.KQ), 999999.KS, price 70000.0")
        self.assertEqual(names.annotate(out), out)

    def test_persists_and_reloads(self):
        names.remember({"005930": "삼성전자"})
        names._names = None
        self.assertEqual(names.annotate("005930.KS"), "삼성전자(005930.KS)")

    def test_log_filter_formats_args(self):
        names.remember({"005930": "삼성전자"})
        rec = logging.LogRecord("x", logging.INFO, "f", 1, "sell %s x%d", ("005930.KS", 2), None)
        names.NameFilter().filter(rec)
        self.assertEqual(rec.getMessage(), "sell 삼성전자(005930.KS) x2")


if __name__ == "__main__":
    unittest.main()
