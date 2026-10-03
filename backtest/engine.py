"""Replay engine: no-lookahead, multi-symbol, in-memory backtesting."""

from __future__ import annotations

from collections import deque
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .types import Bar, EquityPoint, Fill, PeriodView, Snapshot, SymbolView

Strategy = Callable[[Snapshot], Dict[str, int]]

_WINDOW = 300  # 5-minute window size in seconds
_FULL_WINDOW_MASK = 0b11111


class _WindowAcc:
    """Incremental 5-minute aggregation for one symbol."""

    __slots__ = ("start", "mask", "open", "high", "low", "close", "volume")

    def __init__(self, start: int) -> None:
        self.start = start
        self.mask = 0
        self.open = 0
        self.high = 0
        self.low = 0
        self.close = 0
        self.volume = 0


class ReplayEngine:
    """Drives the replay.

    The caller feeds 1-minute bars in batches via :meth:`add_bars`, then
    calls :meth:`finish`. Fills and the equity trajectory accumulate in
    memory and are readable at any time.
    """

    def __init__(self, strategy: Strategy, initial_cash: int) -> None:
        if initial_cash < 0:
            raise ValueError("initial_cash must be non-negative")
        self._strategy = strategy
        self._cash = initial_cash
        self._positions: Dict[str, int] = {}
        self._fills: List[Fill] = []
        self._equity_curve: List[EquityPoint] = []
        self._finished = False
        # Ingestion state.
        self._last_seen_ts: Optional[int] = None
        self._last_ts_by_symbol: Dict[str, int] = {}
        self._open_group_ts: Optional[int] = None
        self._open_group: List[Bar] = []
        self._closing: List[Bar] = []
        # Published market state.
        self._last_1m: Dict[str, Bar] = {}
        self._closes_1m: Dict[str, "deque[int]"] = {}
        self._last_5m: Dict[str, Bar] = {}
        self._closes_5m: Dict[str, "deque[int]"] = {}
        self._windows: Dict[str, _WindowAcc] = {}
        # Trading state.
        self._pending_targets: Dict[str, int] = {}

    def add_bars(self, bars: Iterable[Bar]) -> None:
        """Append one batch of 1-minute bars (timestamps non-decreasing)."""
        if self._finished:
            raise RuntimeError("replay already finished")
        for bar in bars:
            self._ingest(bar)

    def finish(self) -> None:
        """End the replay: publish the close of the last submitted bars."""
        if self._finished:
            raise RuntimeError("replay already finished")
        if self._open_group:
            self._finalize_open_group()
        if self._closing:
            self._close_phase()
        self._finished = True

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def _ingest(self, bar: Bar) -> None:
        if bar.ts % 60 != 0:
            raise ValueError(f"bar timestamp {bar.ts} is not minute-aligned")
        if self._last_seen_ts is not None and bar.ts < self._last_seen_ts:
            raise ValueError("bars must arrive with non-decreasing timestamps")
        last = self._last_ts_by_symbol.get(bar.symbol)
        if last is not None and bar.ts <= last:
            raise ValueError(
                f"duplicate or out-of-order bar for {bar.symbol} at {bar.ts}"
            )
        self._last_ts_by_symbol[bar.symbol] = bar.ts
        self._last_seen_ts = bar.ts
        if self._open_group and bar.ts > self._open_group_ts:
            self._finalize_open_group()
        self._open_group.append(bar)
        self._open_group_ts = bar.ts

    def _finalize_open_group(self) -> None:
        if self._closing:
            self._close_phase()
        self._execute_opening(self._open_group_ts, self._open_group)
        self._closing = self._open_group
        self._open_group = []

    # ------------------------------------------------------------------
    # Close phase: publish bars, record equity, invoke strategy
    # ------------------------------------------------------------------

    def _close_phase(self) -> None:
        close_time = self._closing[0].ts + 60
        for bar in sorted(self._closing, key=lambda b: b.symbol):
            self._publish_1m(bar)
        self._closing = []
        self._record_equity(close_time)
        self._call_strategy(close_time)

    def _publish_1m(self, bar: Bar) -> None:
        self._last_1m[bar.symbol] = bar
        closes = self._closes_1m.setdefault(bar.symbol, deque(maxlen=3))
        closes.append(bar.close)
        self._aggregate_5m(bar)

    def _aggregate_5m(self, bar: Bar) -> None:
        start = bar.ts - bar.ts % _WINDOW
        win = self._windows.get(bar.symbol)
        if win is None or win.start != start:
            win = _WindowAcc(start)
            self._windows[bar.symbol] = win
        idx = (bar.ts - start) // 60
        if win.mask == 0:
            win.open = bar.open
            win.high = bar.high
            win.low = bar.low
        else:
            if bar.high > win.high:
                win.high = bar.high
            if bar.low < win.low:
                win.low = bar.low
        win.close = bar.close
        win.volume += bar.volume
        win.mask |= 1 << idx
        if win.mask == _FULL_WINDOW_MASK:
            bar5 = Bar(
                symbol=bar.symbol,
                ts=start,
                open=win.open,
                high=win.high,
                low=win.low,
                close=win.close,
                volume=win.volume,
            )
            self._last_5m[bar.symbol] = bar5
            closes = self._closes_5m.setdefault(bar.symbol, deque(maxlen=3))
            closes.append(bar5.close)

    def _record_equity(self, ts: int) -> None:
        positions = tuple(
            sorted((sym, qty) for sym, qty in self._positions.items() if qty != 0)
        )
        equity = self._cash + sum(
            qty * self._last_1m[sym].close for sym, qty in positions
        )
        self._equity_curve.append(EquityPoint(ts, self._cash, positions, equity))

    def _call_strategy(self, ts: int) -> None:
        snapshot = self._build_snapshot(ts)
        targets = self._strategy(snapshot)
        if not isinstance(targets, dict):
            raise TypeError("strategy must return a dict of symbol -> target qty")
        expected = {view.symbol for view in snapshot.symbols}
        if set(targets) != expected:
            raise ValueError(
                "strategy must return a target for every snapshot symbol "
                f"(expected {sorted(expected)}, got {sorted(targets)})"
            )
        for symbol, target in targets.items():
            if type(target) is not int or target < 0:
                raise ValueError(
                    f"target for {symbol} must be a non-negative int, got {target!r}"
                )
            self._pending_targets[symbol] = target

    def _build_snapshot(self, ts: int) -> Snapshot:
        views = []
        for symbol in sorted(self._last_1m):
            closes1 = self._closes_1m[symbol]
            sma1 = sum(closes1) / 3 if len(closes1) == 3 else None
            bar5 = self._last_5m.get(symbol)
            closes5 = self._closes_5m.get(symbol)
            sma5 = sum(closes5) / 3 if closes5 is not None and len(closes5) == 3 else None
            views.append(
                SymbolView(
                    symbol=symbol,
                    m1=PeriodView(self._last_1m[symbol], sma1),
                    m5=PeriodView(bar5, sma5),
                )
            )
        positions = tuple(
            sorted((sym, qty) for sym, qty in self._positions.items() if qty != 0)
        )
        return Snapshot(ts, self._cash, positions, tuple(views))

    # ------------------------------------------------------------------
    # Opening phase: execute pending targets against opening bars
    # ------------------------------------------------------------------

    def _execute_opening(self, ts: int, group: List[Bar]) -> None:
        if not self._pending_targets:
            return
        bars_by_symbol = {bar.symbol: bar for bar in group}
        # Sells first, then buys; both phases iterate in symbol name order.
        for symbol in sorted(bars_by_symbol):
            target = self._pending_targets.get(symbol)
            if target is None:
                continue
            held = self._positions.get(symbol, 0)
            if target < held:
                qty = held - target
                price = bars_by_symbol[symbol].open
                self._cash += qty * price
                self._set_position(symbol, target)
                self._fills.append(Fill(ts, symbol, "sell", qty, price))
        for symbol in sorted(bars_by_symbol):
            target = self._pending_targets.get(symbol)
            if target is None:
                continue
            held = self._positions.get(symbol, 0)
            if target > held:
                price = bars_by_symbol[symbol].open
                qty = min(target - held, self._cash // price) if price > 0 else 0
                if qty > 0:
                    self._cash -= qty * price
                    self._set_position(symbol, held + qty)
                    self._fills.append(Fill(ts, symbol, "buy", qty, price))
        for symbol in bars_by_symbol:
            self._pending_targets.pop(symbol, None)

    def _set_position(self, symbol: str, qty: int) -> None:
        if qty == 0:
            self._positions.pop(symbol, None)
        else:
            self._positions[symbol] = qty

    @property
    def fills(self) -> List[Fill]:
        return list(self._fills)

    @property
    def equity_curve(self) -> List[EquityPoint]:
        return list(self._equity_curve)

    @property
    def cash(self) -> int:
        return self._cash

    @property
    def positions(self) -> Dict[str, int]:
        return dict(self._positions)
