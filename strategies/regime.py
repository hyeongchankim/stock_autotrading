"""Detects whether a symbol is trending or range-bound using ADX, so the
engine can restrict new entries to the strategy category suited to the
current regime instead of running trend-following and mean-reversion
strategies at once and having them contradict each other.
"""
from __future__ import annotations

import pandas as pd

from strategies.base import StrategyCategory
from utils.indicators import adx


class RegimeFilter:
    def __init__(
        self,
        adx_period: int = 14,
        trend_threshold: float = 25.0,
        range_threshold: float = 20.0,
    ):
        if range_threshold > trend_threshold:
            raise ValueError("range_threshold must be <= trend_threshold")
        self.adx_period = adx_period
        self.trend_threshold = trend_threshold
        self.range_threshold = range_threshold

    def detect(self, ohlcv: pd.DataFrame) -> StrategyCategory | None:
        """Returns the strategy category allowed to open new positions right
        now, or None in the ambiguous zone between range_threshold and
        trend_threshold (no new entries there - avoids whipsaws).
        """
        if len(ohlcv) < self.adx_period * 2:
            return None

        current_adx = float(adx(ohlcv, self.adx_period).iloc[-1])
        if current_adx >= self.trend_threshold:
            return StrategyCategory.TREND_FOLLOWING
        if current_adx <= self.range_threshold:
            return StrategyCategory.MEAN_REVERSION
        return None
