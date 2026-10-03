"""Hand-computable checks: 5m windows, missing minutes, SMA warm-up."""

from backtest import ReplayEngine
from conftest import make_bar


def run_and_collect(bars, batches=None):
    """Replay `bars`, capturing every snapshot the strategy sees."""
    snapshots = []

    def strat(snap):
        snapshots.append(snap)
        return {}

    eng = ReplayEngine(1_000_000, strat)
    for batch in batches if batches is not None else [bars]:
        eng.add_bars(batch)
    eng.finish()
    return eng, snapshots


def window_bars(start, base):
    """Five 1m bars for window [start, start+300); distinct hand-checkable values."""
    return [
        make_bar("X", start + 60 * i, base + i, high=base + i + 2,
                 low=base + i - 1, close=base + i + 1, volume=100 * (i + 1))
        for i in range(5)
    ]


def test_5m_window_aggregation_and_boundary():
    bars = window_bars(0, 10)  # closes 11..15, window bar published at t=300
    _, snaps = run_and_collect(bars)

    # Strategy is called once per close: at t=60,120,180,240,300.
    assert [s.ts for s in snaps] == [60, 120, 180, 240, 300]

    last = snaps[-1].symbols["X"]
    bar5 = last.bar_5m
    assert bar5 is not None
    assert (bar5.ts, bar5.open, bar5.high, bar5.low, bar5.close, bar5.volume) == (
        0, 10, 16, 9, 15, 1500,
    )
    # Only one 5m bar so far -> SMA3(5m) not warmed up.
    assert last.sma3_5m is None


def test_missing_minute_suppresses_whole_window():
    bars = window_bars(0, 10)                       # complete window -> published
    bars += [make_bar("X", t, 20) for t in (300, 360, 480, 540)]  # gap at 420
    bars += window_bars(600, 30)                    # next window complete
    _, snaps = run_and_collect(bars)
    by_ts = {s.ts: s for s in snaps}

    # Close of the gapped window's last bar (t=540 -> event 600):
    # no new 5m bar; latest published is still the first window.
    assert by_ts[600].symbols["X"].bar_5m.ts == 0
    # The gapped window is never published, and not back-filled across windows.
    published = {s.symbols["X"].bar_5m.ts for s in snaps if s.symbols["X"].bar_5m}
    assert published == {0, 600}
    # Window [600,900) published at its end (event 900).
    assert by_ts[900].symbols["X"].bar_5m.ts == 600
    assert by_ts[900].symbols["X"].bar_5m.close == 35


def test_sma3_warmup_and_gap_does_not_reset():
    bars = window_bars(0, 10)                        # closes 11,12,13,14,15
    bars += [make_bar("X", 600, 20, close=20)]       # gap 300..540
    _, snaps = run_and_collect(bars)
    by_ts = {s.ts: s for s in snaps}

    assert by_ts[120].symbols["X"].sma3_1m is None   # only 2 closes published
    assert by_ts[180].symbols["X"].sma3_1m == 12.0   # (11+12+13)/3
    assert by_ts[300].symbols["X"].sma3_1m == 14.0   # (13+14+15)/3
    # Gap minutes do not reset the count: closes 14,15,20 -> 49/3.
    assert abs(by_ts[660].symbols["X"].sma3_1m - (14 + 15 + 20) / 3) < 1e-9


def test_sma3_5m_after_three_windows():
    bars = window_bars(0, 10) + window_bars(600, 30) + window_bars(1200, 50)
    _, snaps = run_and_collect(bars)
    by_ts = {s.ts: s for s in snaps}

    assert by_ts[900].symbols["X"].sma3_5m is None   # only 2 windows
    # Window closes: 15, 35, 55 -> mean 35.
    assert by_ts[1500].symbols["X"].sma3_5m == 35.0


def test_symbol_absent_until_first_published_bar():
    bars = [make_bar("A", t, 100) for t in (0, 60, 120)]
    bars += [make_bar("B", 120, 50)]
    _, snaps = run_and_collect(bars)
    by_ts = {s.ts: s for s in snaps}

    assert set(by_ts[120].symbols) == {"A"}          # B has no published bar yet
    assert set(by_ts[180].symbols) == {"A", "B"}
    assert by_ts[180].symbols["B"].bar_1m.ts == 120
    assert by_ts[180].symbols["B"].sma3_1m is None
