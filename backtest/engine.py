"""Multi-asset, no-lookahead replay engine.

Timeline model
--------------
A 1-minute bar with open time ``t`` opens at ``t`` and closes at ``t + 60``.
Events are processed in increasing event time. At each event time ``e``:

1. All bars with open time ``e - 60`` close: their 1m bars are published,
   completed 5m windows are published, and SMA3 indicators are updated.
2. If anything closed, one equity point is recorded (before open fills) and
   the strategy is called once with the resulting snapshot; returned targets
   override any unexecuted targets for those symbols.
3. Pending targets are executed against bars that open at ``e`` (all sells
   first, then buys, each in symbol lexicographic order). A target may be
   filled at the same timestamp it was decided, but never at the open price
   of the bar that just closed.

Bars arrive in globally non-decreasing open-time order (same-timestamp
symbols in any order, possibly spanning batches). An event time ``e`` is
only processed once a bar with open time greater than ``e`` has been seen
(or ``finish()`` is called), so results are independent of batching and of
intra-timestamp ordering.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

from .types import (
    Bar,
    EquityPoint,
    Fill,
    PublishedBar,
    Snapshot,
    Strategy,
    SymbolFrame,
)

_WINDOW = 300  # 5 minutes in seconds
_MINUTE = 60
_WINDOW_MINUTES = frozenset(
    offset * _MINUTE for offset in range(_WINDOW // _MINUTE)
)


@dataclass
class _Window5m:
    """Incremental aggregation state for one symbol's current 5m window."""

    start: int
    minutes: Set[int] = field(default_factory=set)
    open: int = 0
    high: int = 0
    low: int = 0
    close: int = 0
    volume: int = 0


class ReplayEngine:
    """Multi-asset, no-lookahead replay engine."""

    def __init__(self, initial_cash: int, strategy: Strategy) -> None:
        if initial_cash < 0:
            raise ValueError("initial_cash must be non-negative")
        self._cash = initial_cash
        self._strategy = strategy
        self._positions: Dict[str, int] = {}
        self._targets: Dict[str, int] = {}

        self._bars_by_ts: Dict[int, Dict[str, Bar]] = {}
        self._open_times: List[int] = []
        self._open_idx = 0
        self._close_idx = 0
        self._max_seen_ts: Optional[int] = None
        self._seen_keys: Set[Tuple[str, int]] = set()
        self._finished = False

        self._latest_1m: Dict[str, PublishedBar] = {}
        self._latest_5m: Dict[str, PublishedBar] = {}
        self._closes_1m: Dict[str, deque] = {}
        self._closes_5m: Dict[str, deque] = {}
        self._count_1m: Dict[str, int] = {}
        self._count_5m: Dict[str, int] = {}
        self._windows: Dict[str, _Window5m] = {}

        self._fills: List[Fill] = []
        self._equity_curve: List[EquityPoint] = []

    # ------------------------------------------------------------------ input

    def add_bars(self, bars: Iterable[Bar]) -> None:
        """Append a batch of 1-minute bars (any intra-batch order)."""
        if self._finished:
            raise ValueError("replay already finished")
        batch_max: Optional[int] = None
        for bar in bars:
            ts = bar.ts
            if self._max_seen_ts is not None and ts < self._max_seen_ts:
                raise ValueError("bars must arrive in non-decreasing ts order")
            key = (bar.symbol, ts)
            if key in self._seen_keys:
                raise ValueError(f"duplicate bar for {key}")
            self._seen_keys.add(key)
            self._bars_by_ts.setdefault(ts, {})[bar.symbol] = bar
            if not self._open_times or self._open_times[-1] != ts:
                self._open_times.append(ts)
            self._max_seen_ts = ts
            batch_max = ts
        if batch_max is not None:
            # Event times strictly below the newest open time are complete.
            self._drain(batch_max)

    def finish(self) -> None:
        """Signal end of input; process the final bar's close."""
        if self._finished:
            return
        self._finished = True
        self._drain(None)

    # ---------------------------------------------------------------- outputs

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
    def positions(self) -> Mapping[str, int]:
        return dict(self._positions)

    # -------------------------------------------------------------- event loop

    def _drain(self, limit: Optional[int]) -> None:
        """Process all events with event time < limit (all if limit is None)."""
        times = self._open_times
        end = len(times) if limit is None else bisect_left(times, limit)
        while True:
            open_at = times[self._open_idx] if self._open_idx < end else None
            close_at = (
                times[self._close_idx] + _MINUTE if self._close_idx < end else None
            )
            if open_at is None and close_at is None:
                return
            if open_at is None:
                event = close_at
            elif close_at is None:
                event = open_at
            else:
                event = min(open_at, close_at)
            has_close = close_at == event
            has_open = open_at == event
            if has_close:
                self._close_idx += 1
            if has_open:
                self._open_idx += 1
            self._process_event(event, has_close, has_open)

    def _process_event(self, event: int, has_close: bool, has_open: bool) -> None:
        if has_close:
            closed = self._bars_by_ts.pop(event - _MINUTE)
            for symbol in sorted(closed):
                self._publish_1m(closed[symbol])
            self._record_equity(event)
            snapshot = self._make_snapshot(event)
            targets = self._strategy(snapshot)
            if targets:
                for symbol, qty in targets.items():
                    if symbol not in snapshot.symbols:
                        continue
                    if not isinstance(qty, int) or qty < 0:
                        raise ValueError(
                            f"target for {symbol} must be a non-negative int"
                        )
                    self._targets[symbol] = qty
        if has_open and self._targets:
            opens = self._bars_by_ts.get(event)
            if opens:
                self._execute(event, opens)

    # -------------------------------------------------------------- publishing

    def _publish_1m(self, bar: Bar) -> None:
        symbol = bar.symbol
        self._latest_1m[symbol] = PublishedBar(
            symbol, bar.ts, bar.open, bar.high, bar.low, bar.close, bar.volume
        )
        closes = self._closes_1m.setdefault(symbol, deque(maxlen=3))
        closes.append(bar.close)
        self._count_1m[symbol] = self._count_1m.get(symbol, 0) + 1

        window_start = (bar.ts // _WINDOW) * _WINDOW
        win = self._windows.get(symbol)
        if win is None or win.start != window_start:
            win = _Window5m(start=window_start, open=bar.open, high=bar.high,
                            low=bar.low)
            self._windows[symbol] = win
        win.minutes.add(bar.ts - window_start)
        win.high = max(win.high, bar.high)
        win.low = min(win.low, bar.low)
        win.close = bar.close
        win.volume += bar.volume
        if win.minutes == _WINDOW_MINUTES:
            published = PublishedBar(
                symbol, window_start, win.open, win.high, win.low,
                win.close, win.volume,
            )
            self._latest_5m[symbol] = published
            closes5 = self._closes_5m.setdefault(symbol, deque(maxlen=3))
            closes5.append(published.close)
            self._count_5m[symbol] = self._count_5m.get(symbol, 0) + 1

    @staticmethod
    def _sma3(closes: Optional[deque], count: int) -> Optional[float]:
        if closes is None or count < 3:
            return None
        return sum(closes) / 3.0

    def _make_snapshot(self, event: int) -> Snapshot:
        frames: Dict[str, SymbolFrame] = {}
        for symbol in sorted(self._latest_1m):
            frames[symbol] = SymbolFrame(
                bar_1m=self._latest_1m[symbol],
                sma3_1m=self._sma3(
                    self._closes_1m.get(symbol),
                    self._count_1m.get(symbol, 0),
                ),
                bar_5m=self._latest_5m.get(symbol),
                sma3_5m=self._sma3(
                    self._closes_5m.get(symbol),
                    self._count_5m.get(symbol, 0),
                ),
            )
        return Snapshot(
            ts=event,
            symbols=frames,
            cash=self._cash,
            positions=dict(self._positions),
        )

    # ---------------------------------------------------------------- trading

    def _record_equity(self, event: int) -> None:
        equity = self._cash
        for symbol, qty in self._positions.items():
            last = self._latest_1m.get(symbol)
            if last is not None:
                equity += qty * last.close
        self._equity_curve.append(
            EquityPoint(
                ts=event,
                cash=self._cash,
                positions=dict(self._positions),
                equity=equity,
            )
        )

    def _set_position(self, symbol: str, qty: int) -> None:
        if qty:
            self._positions[symbol] = qty
        else:
            self._positions.pop(symbol, None)

    def _execute(self, event: int, opens: Mapping[str, Bar]) -> None:
        symbols = sorted(self._targets)
        for symbol in symbols:  # sells first, releasing cash for buys
            target = self._targets[symbol]
            bar = opens.get(symbol)
            if bar is None:
                continue
            position = self._positions.get(symbol, 0)
            if target < position:
                qty = position - target
                self._cash += qty * bar.open
                self._set_position(symbol, target)
                self._fills.append(Fill(event, symbol, "sell", qty, bar.open))
                del self._targets[symbol]
        for symbol in symbols:  # then buys, limited by remaining cash
            target = self._targets.get(symbol)
            if target is None:
                continue
            bar = opens.get(symbol)
            if bar is None:
                continue
            position = self._positions.get(symbol, 0)
            if target > position:
                want = target - position
                if bar.open > 0:
                    qty = min(want, self._cash // bar.open)
                else:
                    qty = want
                if qty:
                    self._cash -= qty * bar.open
                    self._set_position(symbol, position + qty)
                    self._fills.append(Fill(event, symbol, "buy", qty, bar.open))
                del self._targets[symbol]  # unfilled remainder is cancelled
            elif target == position:
                del self._targets[symbol]
