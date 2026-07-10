"""Confirms an entry signal with a volume check, so breakouts on light,
unconvincing volume don't get taken as readily as breakouts backed by a
real surge in participation.
"""
from __future__ import annotations

import pandas as pd


class VolumeFilter:
    def __init__(self, window: int = 20, multiplier: float = 1.5):
        self.window = window
        self.multiplier = multiplier

    def confirms(self, ohlcv: pd.DataFrame) -> bool:
        """Returns True if today's volume is at least multiplier x the
        average of the prior window days. Returns True (no block) when
        there isn't enough history yet to judge.
        """
        if len(ohlcv) < self.window + 1:
            return True

        avg_volume = float(ohlcv["volume"].iloc[-self.window - 1 : -1].mean())
        today_volume = float(ohlcv["volume"].iloc[-1])
        if avg_volume <= 0:
            return True
        return today_volume >= avg_volume * self.multiplier
