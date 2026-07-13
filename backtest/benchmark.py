"""Buy & Hold benchmark: equal-weight allocation across the watchlist at the
start of the backtest window, held with no trading until the end.

Every strategy result should be compared against this. A strategy that adds
entry/exit logic, risk rules, and complexity but still can't beat simply
buying and holding the same universe isn't earning its keep - the logic is
filtering out some of the gain, not adding edge.
"""
from __future__ import annotations

import pandas as pd


def run_buy_and_hold(
    symbol_data: dict[str, pd.DataFrame],
    seed_capital: float,
    start_bar_index: int,
    commission_pct: float = 0.0,
) -> pd.DataFrame:
    """Equal-weights seed_capital across symbol_data at the first trade date
    (same warmup offset the strategy backtest uses), holds to the end with
    no rebalancing, and returns a daily equity curve for direct comparison
    against BacktestRunner.run's output.
    """
    symbols = list(symbol_data.keys())
    if not symbols:
        raise ValueError("symbol_data is empty")

    all_dates = sorted(set().union(*(df.index for df in symbol_data.values())))
    trade_dates = all_dates[start_bar_index:]
    if not trade_dates:
        raise ValueError("start_bar_index is beyond available history")

    entry_date = trade_dates[0]
    allocation_per_symbol = seed_capital / len(symbols)

    shares: dict[str, int] = {}
    cash = seed_capital
    for symbol, df in symbol_data.items():
        if entry_date not in df.index:
            continue
        entry_price = float(df.loc[entry_date, "close"])
        effective_price = entry_price * (1 + commission_pct / 100)
        qty = int(allocation_per_symbol // effective_price)
        shares[symbol] = qty
        cash -= qty * effective_price

    equity_curve = []
    for current_date in trade_dates:
        holdings_value = 0.0
        for symbol, qty in shares.items():
            available = symbol_data[symbol][symbol_data[symbol].index <= current_date]
            if available.empty:
                continue
            holdings_value += qty * float(available["close"].iloc[-1])
        equity_curve.append({"date": current_date, "equity": cash + holdings_value})

    return pd.DataFrame(equity_curve).set_index("date")
