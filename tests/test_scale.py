"""Scale check: 50 symbols x 4000 minutes = 200k bars, hold-position strategy."""

import time

from backtest import Bar, ReplayEngine


def test_scale_50_symbols_200k_bars_under_20s():
    symbols = [f"S{i:02d}" for i in range(50)]
    minutes = 4000

    def gen():
        for i in range(minutes):
            ts = i * 60
            for j, sym in enumerate(symbols):
                price = 10_000 + (i * 3 + j * 17) % 500
                yield Bar(sym, ts, price, price + 5, price - 5, price + 1, 100)

    eng = ReplayEngine(10**12, lambda snap: dict(snap.positions))
    start = time.monotonic()
    batch = []
    for bar in gen():
        batch.append(bar)
        if len(batch) >= 10_000:
            eng.add_bars(batch)
            batch = []
    if batch:
        eng.add_bars(batch)
    eng.finish()
    elapsed = time.monotonic() - start

    assert len(eng.equity_curve) == minutes  # one point per close event
    assert elapsed < 20, f"replay took {elapsed:.1f}s"
