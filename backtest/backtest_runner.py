"""Replays historical OHLCV bar-by-bar through the same TradingEngine used
for live/paper trading, so strategy behavior is identical in both modes.
"""
from __future__ import annotations

import pandas as pd

from engine.trading_engine import TradingEngine


class BacktestRunner:
    def __init__(self, engine: TradingEngine, min_bars: int = 30):
        self.engine = engine
        self.min_bars = min_bars

    def run(self, symbol_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """symbol_data: {symbol: full historical OHLCV DataFrame indexed by date}.
        Returns an equity curve DataFrame indexed by date with an 'equity' column.
        """
        all_dates = sorted(set().union(*(df.index for df in symbol_data.values())))
        equity_curve = []

        for current_date in all_dates[self.min_bars :]:
            windows = {
                symbol: df[df.index <= current_date]
                for symbol, df in symbol_data.items()
                if len(df[df.index <= current_date]) >= self.min_bars
            }
            if not windows:
                continue

            as_of = current_date.date() if hasattr(current_date, "date") else current_date
            equity = self.engine.run_once(as_of=as_of, precomputed_windows=windows)
            equity_curve.append({"date": current_date, "equity": equity})

        return pd.DataFrame(equity_curve).set_index("date")
