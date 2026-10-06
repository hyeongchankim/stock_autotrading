"""Is the Korean market open today? The scheduler only knows weekdays, so on a
holiday (e.g. 2026-10-05, the substitute holiday for 개천절) the bot used to run its
whole day of cycles against stale bars and after-hours quotes, and the forward-test
log recorded a junk day.

No holiday table to maintain: KIS's daily chart simply has no bar for a day the market
was closed, so "the newest bar of a liquid reference stock is not today" means closed.
It only ever says closed when it POSITIVELY sees that - any data problem returns False
(= carry on) so a KIS hiccup can't silently switch trading off.
"""
from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger("data.market_calendar")

REFERENCE_SYMBOL = "005930.KS"  # 삼성전자 trades every open day and is never halted for long


def market_closed_today(feed, today: date | None = None, reference: str = REFERENCE_SYMBOL) -> bool:
    """True only if `feed` (any DataFeedBase) shows the reference stock's latest daily bar
    is dated BEFORE today. False when the bar is today's, or when it can't be told."""
    today = today or date.today()
    try:
        bars = feed.get_ohlcv(reference, "1d", 5)
        latest = bars.index[-1].date()
    except Exception as exc:  # noqa: BLE001 - unknown is not "closed"
        logger.warning("market calendar: could not read %s bars (%s) - assuming the market is open", reference, exc)
        return False
    return latest < today
