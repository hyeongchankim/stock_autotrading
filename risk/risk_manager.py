"""Account-level risk rules: stop-loss, take-profit, position sizing, daily loss limit."""
from __future__ import annotations

from datetime import date


class RiskManager:
    """Enforces the account's risk rules. Strategies never decide position size
    or exits directly - they only vote BUY/SELL/HOLD, and RiskManager decides
    whether/how much to act on that vote.

    - stop_loss_pct: force-sell a position once it drops this % below entry price
    - take_profit_pct: force-sell a position once it rises this % above entry price
    - position_size_pct: max fraction of total equity allocated to one symbol per entry
    - daily_max_loss_pct: once today's realized loss reaches this % of seed capital,
      no new entries are allowed for the rest of the day (protective exits still allowed)

    A trailing-stop variant (arm at take-profit, trail the peak instead of
    exiting immediately) was tried and backtested worse on this strategy mix
    across 10 symbols / 5 years - immediate take-profit consistently won
    because these strategies rely on fast capital turnover (position sizing
    caps you at ~3 concurrent symbols at 30% each), so freeing cash for the
    next signal beats holding out for a bigger single-trade gain. Kept simple
    and immediate on purpose; see backtest results before changing this again.
    """

    def __init__(
        self,
        seed_capital: float,
        stop_loss_pct: float = 0.10,
        take_profit_pct: float = 0.07,
        position_size_pct: float = 0.30,
        daily_max_loss_pct: float = 0.30,
    ):
        self.seed_capital = seed_capital
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.position_size_pct = position_size_pct
        self.daily_max_loss_amount = seed_capital * daily_max_loss_pct

        self._current_day: date | None = None
        self.daily_realized_pnl = 0.0
        self.trading_halted_today = False

    def roll_to_day(self, as_of: date) -> None:
        """Resets daily counters when the trading day changes."""
        if self._current_day != as_of:
            self._current_day = as_of
            self.daily_realized_pnl = 0.0
            self.trading_halted_today = False

    def can_open_new_position(self) -> bool:
        return not self.trading_halted_today

    def calc_position_size(self, total_equity: float, price: float) -> int:
        if price <= 0:
            return 0
        allocation = total_equity * self.position_size_pct
        return int(allocation // price)

    def clear_peak_price(self, symbol: str) -> None:
        """No-op retained so the engine can call it unconditionally regardless
        of which risk model is active.
        """

    def check_exit_trigger(self, symbol: str, avg_entry_price: float, current_price: float) -> str | None:
        """Returns 'STOP_LOSS', 'TAKE_PROFIT', or None."""
        change_pct = (current_price - avg_entry_price) / avg_entry_price
        if change_pct <= -self.stop_loss_pct:
            return "STOP_LOSS"
        if change_pct >= self.take_profit_pct:
            return "TAKE_PROFIT"
        return None

    def record_realized_pnl(self, pnl: float) -> None:
        self.daily_realized_pnl += pnl
        if self.daily_realized_pnl <= -self.daily_max_loss_amount:
            self.trading_halted_today = True

    def to_dict(self) -> dict:
        """Snapshot of today's counters, for StateStore to persist across
        separate process runs (a single `python main.py --mode paper`
        invocation has no memory of earlier runs the same day otherwise, so
        the daily loss halt would never actually trigger under a scheduler).
        """
        return {
            "date": self._current_day.isoformat() if self._current_day else None,
            "daily_realized_pnl": self.daily_realized_pnl,
            "trading_halted_today": self.trading_halted_today,
        }

    def restore(self, state: dict) -> None:
        """Loads a snapshot from to_dict. Safe to call with {} (no-op). If the
        saved date isn't today, the stale counters are still loaded here but
        the next roll_to_day call (always made at the start of run_once)
        resets them before anything reads them - so callers don't need to
        check the date themselves.
        """
        date_str = state.get("date")
        if not date_str:
            return
        self._current_day = date.fromisoformat(date_str)
        self.daily_realized_pnl = state.get("daily_realized_pnl", 0.0)
        self.trading_halted_today = state.get("trading_halted_today", False)
