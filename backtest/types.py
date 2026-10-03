"""Data types for the multi-asset replay backtest engine.

All timestamps are UTC epoch seconds. Prices and cash are integer cents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Protocol


@dataclass(frozen=True)
class Bar:
    """One 1-minute OHLCV bar. `ts` is the open time (UTC epoch seconds)."""

    symbol: str
    ts: int
    open: int
    high: int
    low: int
    close: int
    volume: int


@dataclass(frozen=True)
class PublishedBar:
    """A bar published to the strategy (1m or aggregated 5m)."""

    symbol: str
    ts: int  # open time of the bar / window
    open: int
    high: int
    low: int
    close: int
    volume: int


@dataclass(frozen=True)
class SymbolFrame:
    """Latest published state for one symbol in a snapshot."""

    bar_1m: Optional[PublishedBar]
    sma3_1m: Optional[float]
    bar_5m: Optional[PublishedBar]
    sma3_5m: Optional[float]


@dataclass(frozen=True)
class Snapshot:
    """Immutable view handed to the strategy at each close time."""

    ts: int
    symbols: Mapping[str, SymbolFrame]
    cash: int
    positions: Mapping[str, int]


@dataclass(frozen=True)
class Fill:
    """One executed trade."""

    ts: int
    symbol: str
    side: str  # "buy" or "sell"
    qty: int
    price: int


@dataclass(frozen=True)
class EquityPoint:
    """Portfolio state recorded at each close time, before open fills."""

    ts: int
    cash: int
    positions: Mapping[str, int]
    equity: int


class Strategy(Protocol):
    """Deterministic function of a single snapshot.

    Returns target share counts (non-negative ints) for symbols in the
    snapshot. Symbols omitted keep no target.
    """

    def __call__(self, snapshot: Snapshot) -> Mapping[str, int]: ...
