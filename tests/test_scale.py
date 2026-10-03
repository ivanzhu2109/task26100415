"""Scale test: 50 symbols x 200k 1-minute bars must replay within 20s."""

import time

from backtest import Bar, ReplayEngine

SYMBOL_COUNT = 50
BAR_COUNT = 200_000
TIME_LIMIT_S = 20.0


def make_bars():
    minutes = BAR_COUNT // SYMBOL_COUNT  # 4000 distinct timestamps
    symbols = [f"S{i:03d}" for i in range(SYMBOL_COUNT)]
    bars = []
    for i in range(minutes):
        ts = 60 * i
        for j, sym in enumerate(symbols):
            price = 10_000 + (i * 7 + j * 131) % 5_000
            bars.append(Bar(sym, ts, price, price + 5, price - 5, price + 1, 100))
    return bars


def hold_positions(snapshot):
    return {v.symbol: snapshot.position_of(v.symbol) for v in snapshot.symbols}


def test_replay_50_symbols_200k_bars_under_20_seconds():
    bars = make_bars()
    assert len(bars) == BAR_COUNT

    engine = ReplayEngine(hold_positions, 1_000_000)
    start = time.perf_counter()
    batch = 10_000
    for i in range(0, len(bars), batch):
        engine.add_bars(bars[i : i + batch])
    engine.finish()
    elapsed = time.perf_counter() - start

    assert engine.equity_curve, "expected a non-empty equity curve"
    assert elapsed < TIME_LIMIT_S, f"replay took {elapsed:.1f}s (> {TIME_LIMIT_S}s)"
