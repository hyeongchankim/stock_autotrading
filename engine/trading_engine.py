"""Ties data feed, strategies, risk manager, and broker together for one watchlist.

Signal combination policy:
- a RegimeFilter (ADX-based) decides which strategy category - trend-following
  or mean-reversion - is allowed to open new positions right now; in the
  ambiguous zone between thresholds, no new entries are taken at all
- within the active category, entry_mode="all" requires every enabled
  strategy in that category to agree before opening a position (higher
  conviction, fewer whipsaws); entry_mode="any" only needs one
- an optional VolumeFilter can additionally require today's volume to
  exceed its recent average before an entry is taken
- exits are never regime-gated or volume-gated: any enabled strategy voting
  SELL closes an existing position, regardless of category
- stop-loss / take-profit checks always run first and take priority over
  strategy-driven exits
- only one open position per symbol is tracked at a time

Entry ordering: all exits across the whole watchlist run first (freeing up
cash), then all entry candidates for this cycle are gathered before any of
them execute. If rank_entries_by_momentum is enabled, candidates are sorted
by trailing return (strongest first) so limited capital goes to the
strongest setups when multiple symbols signal on the same day, instead of
whichever happens to come first in watchlist order.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from broker.base import BrokerBase
from data.base import DataFeedBase
from risk.risk_manager import RiskManager
from strategies.base import Signal, Strategy
from strategies.regime import RegimeFilter
from strategies.volume_filter import VolumeFilter
from utils.notify import send_notification

logger = logging.getLogger("engine")


def _broker_label(broker: BrokerBase) -> str:
    """Distinguishes real-money orders from paper/demo/backtest ones in
    notification text, so a Telegram message can never be mistaken for the
    other. KisBroker exposes session.env ("demo"/"real"); anything else
    (MockBroker, backtests) doesn't put real money at risk.
    """
    session = getattr(broker, "session", None)
    env = getattr(session, "env", None)
    return env if env in ("demo", "real") else "mock"


@dataclass
class EntryCandidate:
    symbol: str
    price: float
    strategy_names: str
    momentum: float


class TradingEngine:
    def __init__(
        self,
        broker: BrokerBase,
        data_feed: DataFeedBase,
        strategies: list[Strategy],
        risk_manager: RiskManager,
        watchlist: list[str],
        interval: str = "1d",
        lookback: int = 100,
        regime_filter: RegimeFilter | None = None,
        entry_mode: str = "any",
        volume_filter: VolumeFilter | None = None,
        rank_entries_by_momentum: bool = False,
        momentum_window: int = 20,
    ):
        if entry_mode not in ("all", "any"):
            raise ValueError("entry_mode must be 'all' or 'any'")
        self.broker = broker
        self.data_feed = data_feed
        self.strategies = strategies
        self.risk_manager = risk_manager
        self.watchlist = watchlist
        self.interval = interval
        self.lookback = lookback
        self.regime_filter = regime_filter
        self.entry_mode = entry_mode
        self.volume_filter = volume_filter
        self.rank_entries_by_momentum = rank_entries_by_momentum
        self.momentum_window = momentum_window

    def run_once(
        self,
        as_of: date | None = None,
        precomputed_windows: dict[str, pd.DataFrame] | None = None,
    ) -> float:
        """Runs one trading cycle and returns total equity afterward.

        precomputed_windows lets the backtest runner replay historical bars
        through this exact same logic. Live/paper mode leaves it as None and
        fetches fresh data from the data feed instead.
        """
        as_of = as_of or date.today()
        self.risk_manager.roll_to_day(as_of)

        windows: dict[str, pd.DataFrame] = {}
        if precomputed_windows is not None:
            windows = precomputed_windows
        else:
            for symbol in self.watchlist:
                try:
                    windows[symbol] = self.data_feed.get_ohlcv(symbol, self.interval, self.lookback)
                except Exception as exc:
                    logger.warning("skipping %s: failed to fetch data (%s)", symbol, exc)

        day_prices = {symbol: float(df["close"].iloc[-1]) for symbol, df in windows.items()}
        if hasattr(self.broker, "set_price_lookup"):
            self.broker.set_price_lookup(day_prices)

        # Pass 1: exits for every symbol first, so freed-up cash is
        # available to this same cycle's entries.
        for symbol, ohlcv in windows.items():
            current_price = day_prices[symbol]
            self._check_protective_exit(symbol, current_price)
            self._check_strategy_exit(symbol, ohlcv, current_price)

        # Pass 2: gather every entry candidate this cycle, then rank and
        # execute - rather than greedily entering in watchlist order.
        candidates = []
        for symbol, ohlcv in windows.items():
            candidate = self._find_entry_candidate(symbol, ohlcv, day_prices[symbol])
            if candidate is not None:
                candidates.append(candidate)

        if self.rank_entries_by_momentum:
            candidates.sort(key=lambda c: c.momentum, reverse=True)

        for candidate in candidates:
            self._execute_entry(candidate)

        return self.broker.get_total_equity()

    def _check_protective_exit(self, symbol: str, current_price: float) -> None:
        position = self.broker.get_positions().get(symbol)
        if position is None:
            return

        trigger = self.risk_manager.check_exit_trigger(symbol, position.avg_price, current_price)
        if trigger is None:
            return

        result = self.broker.place_order(symbol, Signal.SELL, position.quantity, current_price)
        env = _broker_label(self.broker)
        if result.filled:
            self.risk_manager.record_realized_pnl(result.realized_pnl)
            self.risk_manager.clear_peak_price(symbol)
            logger.info(
                "%s: %s exit, qty=%s price=%.2f pnl=%.2f",
                symbol, trigger, result.quantity, result.price, result.realized_pnl,
            )
            send_notification(
                f"[{env}] {trigger} exit: {symbol} x{result.quantity} @ {result.price:.0f} "
                f"pnl={result.realized_pnl:+.0f}"
            )
        else:
            # a failed protective exit leaves the position open and
            # unmanaged until the next cycle re-evaluates it - risk-
            # critical enough to always surface, unlike a failed entry.
            logger.warning("%s: %s exit FAILED - %s", symbol, trigger, result.message)
            send_notification(f"[{env}] {trigger} exit FAILED: {symbol} - {result.message}")

    def _check_strategy_exit(self, symbol: str, ohlcv: pd.DataFrame, current_price: float) -> None:
        position = self.broker.get_positions().get(symbol)
        if position is None:
            return

        signals = [s.generate_signal(symbol, ohlcv) for s in self.strategies]
        sell_signals = [s for s in signals if s.signal == Signal.SELL]
        if not sell_signals:
            return

        result = self.broker.place_order(symbol, Signal.SELL, position.quantity, current_price)
        env = _broker_label(self.broker)
        if result.filled:
            self.risk_manager.record_realized_pnl(result.realized_pnl)
            self.risk_manager.clear_peak_price(symbol)
            logger.info(
                "%s: strategy exit (%s), qty=%s price=%.2f pnl=%.2f",
                symbol, sell_signals[0].strategy_name, result.quantity, result.price, result.realized_pnl,
            )
            send_notification(
                f"[{env}] strategy exit ({sell_signals[0].strategy_name}): {symbol} x{result.quantity} "
                f"@ {result.price:.0f} pnl={result.realized_pnl:+.0f}"
            )
        else:
            logger.warning("%s: strategy exit FAILED - %s", symbol, result.message)
            send_notification(f"[{env}] strategy exit FAILED: {symbol} - {result.message}")

    def _find_entry_candidate(
        self, symbol: str, ohlcv: pd.DataFrame, current_price: float
    ) -> EntryCandidate | None:
        if self.broker.get_positions().get(symbol) is not None:
            return None
        if not self.risk_manager.can_open_new_position():
            return None

        active_category = self.regime_filter.detect(ohlcv) if self.regime_filter else None
        if self.regime_filter is not None and active_category is None:
            return None  # ambiguous regime - sit out rather than risk a whipsaw entry

        signals = [s.generate_signal(symbol, ohlcv) for s in self.strategies]
        for sig in signals:
            if sig.signal != Signal.HOLD:
                logger.debug("%s: %s -> %s (%s)", symbol, sig.strategy_name, sig.signal.value, sig.reason)

        strategy_signal_pairs = list(zip(self.strategies, signals))
        candidate_pairs = [
            (s, sig) for s, sig in strategy_signal_pairs
            if active_category is None or s.category == active_category
        ]
        if not candidate_pairs:
            return None

        buy_signals = [sig for _s, sig in candidate_pairs if sig.signal == Signal.BUY]
        if self.entry_mode == "all":
            if len(buy_signals) < len(candidate_pairs):
                return None
        elif not buy_signals:
            return None

        if self.volume_filter is not None and not self.volume_filter.confirms(ohlcv):
            return None

        momentum = 0.0
        if len(ohlcv) > self.momentum_window:
            past_price = float(ohlcv["close"].iloc[-self.momentum_window - 1])
            if past_price > 0:
                momentum = (current_price - past_price) / past_price

        strategy_names = ",".join(sig.strategy_name for sig in buy_signals)
        return EntryCandidate(symbol=symbol, price=current_price, strategy_names=strategy_names, momentum=momentum)

    def _execute_entry(self, candidate: EntryCandidate) -> None:
        # Re-check position/capital state at execution time: an earlier
        # candidate in this same cycle may have already used up the cash.
        if self.broker.get_positions().get(candidate.symbol) is not None:
            return
        if not self.risk_manager.can_open_new_position():
            return

        total_equity = self.broker.get_total_equity()
        quantity = self.risk_manager.calc_position_size(total_equity, candidate.price)
        if quantity <= 0:
            return

        result = self.broker.place_order(candidate.symbol, Signal.BUY, quantity, candidate.price)
        if result.filled:
            logger.info(
                "%s: entry (%s), qty=%s price=%.2f",
                candidate.symbol, candidate.strategy_names, result.quantity, result.price,
            )
            send_notification(
                f"[{_broker_label(self.broker)}] entry ({candidate.strategy_names}): "
                f"{candidate.symbol} x{result.quantity} @ {result.price:.0f}"
            )
        else:
            # not risk-critical the way a failed exit is (worst case: a
            # missed entry, not an unmanaged open position) - log only.
            logger.warning("%s: entry FAILED - %s", candidate.symbol, result.message)
