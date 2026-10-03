"""Data types for the replay backtest library.

All timestamps are integer UTC epoch seconds. For 1-minute input bars the
timestamp is the *open* instant of the bar; the bar closes 60 seconds later.
All prices and cash are integer cents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar. ``ts`` is the open instant (UTC epoch seconds)."""

    symbol: str
    ts: int
    open: int
    high: int
    low: int
    close: int
    volume: int


@dataclass(frozen=True)
class PeriodView:
    """Latest published state for one bar period of one symbol."""

    bar: Optional[Bar]
    sma3: Optional[float]


@dataclass(frozen=True)
class SymbolView:
    """Per-symbol snapshot entry covering both the 1m and 5m periods."""

    symbol: str
    m1: PeriodView
    m5: PeriodView


@dataclass(frozen=True)
class Snapshot:
    """Immutable view handed to the strategy at one decision instant.

    ``ts`` is the instant at which the just-published bars closed.
    ``symbols`` only contains symbols with at least one published 1m bar.
    """

    ts: int
    cash: int
    positions: Tuple[Tuple[str, int], ...]
    symbols: Tuple[SymbolView, ...]

    def position_of(self, symbol: str) -> int:
        for name, qty in self.positions:
            if name == symbol:
                return qty
        return 0

    def view_of(self, symbol: str) -> Optional[SymbolView]:
        for view in self.symbols:
            if view.symbol == symbol:
                return view
        return None


@dataclass(frozen=True)
class Fill:
    """One executed trade at an opening phase."""

    ts: int
    symbol: str
    side: str
    qty: int
    price: int


@dataclass(frozen=True)
class EquityPoint:
    """Portfolio state recorded after a close publication, before fills."""

    ts: int
    cash: int
    positions: Tuple[Tuple[str, int], ...]
    equity: int
