"""Backtest performance metrics beyond raw total return.

Total return alone can be misleading - a single strongly trending backtest
window rewards taking every signal, regardless of whether that would hold up
in a choppier period. Max drawdown and win rate give a fuller, risk-adjusted
picture of whether a strategy change actually improved things.
"""
from __future__ import annotations

import pandas as pd

from broker.base import OrderResult
from strategies.base import Signal


def max_drawdown_pct(equity_curve: pd.Series) -> float:
    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max
    return float(drawdown.min() * 100)


def trade_stats(order_log: list[OrderResult]) -> dict:
    sells = [o for o in order_log if o.filled and o.side == Signal.SELL]
    wins = [o for o in sells if o.realized_pnl > 0]
    return {
        "num_round_trips": len(sells),
        "win_rate_pct": (len(wins) / len(sells) * 100) if sells else 0.0,
        "total_realized_pnl": sum(o.realized_pnl for o in sells),
    }
