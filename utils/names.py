"""Ticker -> Korean company name, so logs and notifications read "삼성전자(005930.KS)" instead of "005930.KS".

Names come for free from the KIS quote responses (screening/universe_quotes.fetch_quotes calls remember()) and are cached
in names_cache.json, so nothing extra is fetched. A symbol with no known name stays as the bare code.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

NAMES_FILE = Path(__file__).resolve().parent.parent / "names_cache.json"
_SYMBOL = re.compile(r"(?<![\w(])(\d{6})\.(KS|KQ)\b")
_names: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _names
    if _names is None:
        try:
            _names = json.loads(NAMES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _names = {}
    return _names


def remember(names: dict[str, str]) -> None:
    """ticker (6 digits, any suffix ignored) -> name; persisted only when something changed."""
    known = _load()
    new = {t.split(".")[0]: n.strip() for t, n in names.items() if n and known.get(t.split(".")[0]) != n.strip()}
    if not new:
        return
    known.update(new)
    try:
        NAMES_FILE.write_text(json.dumps(known, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    except OSError:
        pass  # a name cache must never break a trading cycle


def annotate(text: str) -> str:
    """Rewrites every 'NNNNNN.KS/KQ' in text to '이름(NNNNNN.KS)' when the name is known."""
    known = _load()
    return _SYMBOL.sub(lambda m: f"{known[m[1]]}({m[0]})" if m[1] in known else m[0], text)


class NameFilter:
    """logging.Filter that annotates the message in place (attach to handlers so every logger is covered)."""

    def filter(self, record) -> bool:
        record.msg, record.args = annotate(record.getMessage()), ()
        return True
