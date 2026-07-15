"""KRX minimum price increment (호가단위/tick size) - a static, public
exchange rule, not something fetched from an API. Table is the unified
tiers KRX applies to both KOSPI and KOSDAQ (2023 unification; previously
KOSDAQ had its own, denser table below 5,000 won).

Only tick size is handled here, not the daily ±30% price limit band
(상한가/하한가): every order price this codebase submits is always sourced
from an already-observed real market price (a fetched close, or
get_current_price's live quote) rather than computed synthetically (e.g.
"avg_price * 1.05"), so it's within that day's band by construction - the
exchange wouldn't have let that observed price trade otherwise. Tick
alignment is the real risk instead, since prices pass through float
arithmetic (adjusted closes, indicator calculations) before reaching
place_order.
"""
from __future__ import annotations

_TICK_TABLE = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (float("inf"), 1_000),
)


def tick_size(price: float) -> int:
    """The minimum price increment for price's own tier."""
    for threshold, tick in _TICK_TABLE:
        if price < threshold:
            return tick
    return _TICK_TABLE[-1][1]


def round_to_tick(price: float) -> int:
    """Rounds price to the nearest valid multiple of its own tier's tick
    size. KIS rejects orders priced off-tick, and prices flowing through
    this codebase (moving averages, %-based stop-loss/take-profit levels,
    adjusted-close data) usually aren't tick-aligned by the time they reach
    place_order.
    """
    if price <= 0:
        return 0
    tick = tick_size(price)
    return int(round(price / tick) * tick)
