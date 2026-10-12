"""Dynamic watchlist for the watchlist sleeve: instead of 10 hand-picked fixed
stocks (selection bias), trade what the discovery pipeline's market scan surfaces
right now - the same scan + 1차 필터 the 발굴형 sleeve uses (screening/discover.py),
cut down to what this sleeve can actually afford.

Held positions must stay in the evaluated set even after they drop out of the
scan, otherwise their stop-loss / strategy exits would stop being checked.
"""
from __future__ import annotations

import pandas as pd

from screening.universe_quotes import universe_candidates


def pick_symbols(candidates: pd.DataFrame, max_candidates: int, max_price: float) -> list[str]:
    """Strongest movers first. `max_price` drops stocks whose single share costs
    more than one position's budget - the sizing code would round them to 0 shares
    anyway (a 500k sleeve at 30% per position can't buy a 1.8M-won stock)."""
    affordable = candidates[candidates["price"] <= max_price].sort_values("change_pct", ascending=False)
    return affordable["symbol"].head(max_candidates).tolist()


def merge_with_held(movers: list[str], held) -> list[str]:
    """Movers (strongest first - entries are executed in this order), then any
    held symbol that isn't already one of them."""
    return movers + [s for s in held if s not in movers]


def current_movers(session, universe: dict, disc_cfg: dict, max_candidates: int, max_price: float) -> list[str]:
    """The universe's live quotes (about a dozen KIS calls), filtered exactly like the discovery sleeve's
    1차 필터 and ranked by exact change % - the same candidates the 발굴형 sleeve sees."""
    return pick_symbols(universe_candidates(session, universe, disc_cfg), max_candidates, max_price)
