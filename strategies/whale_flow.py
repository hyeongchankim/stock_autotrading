"""'Whale tracking': follows accumulated foreign + institutional net buying
(외국인/기관 순매수) relative to the stock's own trading turnover.

Requires institutional_net/foreign_net columns on the ohlcv DataFrame,
added by data/krx_investor_feed.py's attach_investor_flow(). Those columns
depend on a KRX Information Data System login (KRX_ID/KRX_PW env vars) -
when they're missing, this strategy holds rather than erroring, so the
engine keeps working with or without that data source configured.
"""
from __future__ import annotations

import pandas as pd

from strategies.base import Signal, Strategy, StrategyCategory, StrategySignal


class WhaleFlowStrategy(Strategy):
    """Sums institutional_net + foreign_net over the trailing window and
    divides by the window's total trading turnover (close * volume) to get
    a turnover-normalized net-buy ratio - comparable across stocks of very
    different sizes, unlike a raw KRW amount.

    net_buy_ratio >= buy_threshold_ratio -> BUY (smart money accumulating)
    net_buy_ratio <= -sell_threshold_ratio -> SELL (smart money distributing)
    """

    name = "whale_flow"
    category = StrategyCategory.TREND_FOLLOWING

    def __init__(self, window: int = 5, buy_threshold_ratio: float = 0.05, sell_threshold_ratio: float = 0.05):
        self.window = window
        self.buy_threshold_ratio = buy_threshold_ratio
        self.sell_threshold_ratio = sell_threshold_ratio

    def min_bars_required(self) -> int:
        return self.window + 1

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame) -> StrategySignal:
        if "institutional_net" not in ohlcv.columns or "foreign_net" not in ohlcv.columns:
            return self._hold(symbol, "no investor flow data available")

        # KRX confirms institutional/foreign net-buy data with roughly a
        # one-day lag - "today"'s row is reliably NaN during market hours
        # (see krx_investor_feed.py's docstring). Anchoring the window on
        # ohlcv's last row would make this strategy hold on every single
        # intraday cycle, every day, since the trailing window would always
        # include that unconfirmed row. Anchor on the last CONFIRMED rows
        # instead, so the signal is based on "most recent settled data" -
        # entries still execute at today's live price via `price` below.
        confirmed = ohlcv.dropna(subset=["institutional_net", "foreign_net"])
        if len(confirmed) < self.window:
            return self._hold(symbol, "insufficient confirmed investor flow history")

        window_slice = confirmed.iloc[-self.window :]
        institutional = window_slice["institutional_net"]
        foreign = window_slice["foreign_net"]

        combined_net = float((institutional + foreign).sum())
        turnover = float((window_slice["close"] * window_slice["volume"]).sum())
        price = float(ohlcv["close"].iloc[-1])

        if turnover <= 0:
            return self._hold(symbol, "no turnover data")

        net_buy_ratio = combined_net / turnover

        if net_buy_ratio >= self.buy_threshold_ratio:
            return StrategySignal(
                symbol, Signal.BUY, self.name, f"{self.window}d whale net-buy ratio {net_buy_ratio:.1%}", price
            )
        if net_buy_ratio <= -self.sell_threshold_ratio:
            return StrategySignal(
                symbol, Signal.SELL, self.name, f"{self.window}d whale net-sell ratio {net_buy_ratio:.1%}", price
            )
        return self._hold(symbol, f"whale net-buy ratio {net_buy_ratio:.1%} inside threshold")
