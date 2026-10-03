"""Hand-computable checks: 5m window boundaries, missing minutes, SMA warmup."""

from backtest import Bar, ReplayEngine


def run_collecting(bars, cash=1_000_000):
    snapshots = []

    def strategy(snapshot):
        snapshots.append(snapshot)
        return {view.symbol: snapshot.position_of(view.symbol) for view in snapshot.symbols}

    engine = ReplayEngine(strategy, cash)
    engine.add_bars(bars)
    engine.finish()
    return engine, snapshots


def test_five_minute_window_boundary_and_warmup():
    # Five consecutive 1m bars of AAA starting at a 5m boundary (ts=0).
    bars = [
        Bar("AAA", 0, 100, 110, 90, 105, 10),
        Bar("AAA", 60, 106, 112, 101, 108, 20),
        Bar("AAA", 120, 109, 115, 107, 111, 30),
        Bar("AAA", 180, 112, 118, 110, 114, 40),
        Bar("AAA", 240, 115, 120, 113, 117, 50),
    ]
    _, snaps = run_collecting(bars)

    # Decisions happen at each close: ts = 60, 120, 180, 240, 300.
    assert [s.ts for s in snaps] == [60, 120, 180, 240, 300]

    # 1m SMA3 warmup: unavailable for the first two published bars.
    assert snaps[0].view_of("AAA").m1.sma3 is None
    assert snaps[1].view_of("AAA").m1.sma3 is None
    assert snaps[2].view_of("AAA").m1.sma3 == (105 + 108 + 111) / 3
    assert snaps[3].view_of("AAA").m1.sma3 == (108 + 111 + 114) / 3
    assert snaps[4].view_of("AAA").m1.sma3 == (111 + 114 + 117) / 3

    # The 5m window is invisible while incomplete: no temporary values.
    for snap in snaps[:4]:
        assert snap.view_of("AAA").m5.bar is None
        assert snap.view_of("AAA").m5.sma3 is None

    # At ts=300 the window [0, 300) is published in full.
    bar5 = snaps[4].view_of("AAA").m5.bar
    assert bar5 is not None
    assert (bar5.symbol, bar5.ts) == ("AAA", 0)
    assert (bar5.open, bar5.high, bar5.low, bar5.close, bar5.volume) == (
        100, 120, 90, 117, 150,
    )
    # Only one 5m bar so far: its SMA3 is still warming up.
    assert snaps[4].view_of("AAA").m5.sma3 is None


def test_missing_minute_kills_window_and_gap_does_not_reset_sma():
    # Minute 120 is missing, so the window [0, 300) must never be published.
    bars = [
        Bar("AAA", 0, 100, 100, 100, 100, 1),
        Bar("AAA", 60, 101, 101, 101, 101, 1),
        Bar("AAA", 180, 103, 103, 103, 103, 1),
        Bar("AAA", 240, 104, 104, 104, 104, 1),
        # A complete second window [300, 600).
        Bar("AAA", 300, 105, 105, 105, 105, 2),
        Bar("AAA", 360, 106, 106, 106, 106, 2),
        Bar("AAA", 420, 107, 107, 107, 107, 2),
        Bar("AAA", 480, 108, 108, 108, 108, 2),
        Bar("AAA", 540, 109, 109, 109, 109, 2),
    ]
    _, snaps = run_collecting(bars)

    # Closes at 60, 120, 240, 300, 360, 420, 480, 540, 600.
    assert [s.ts for s in snaps] == [60, 120, 240, 300, 360, 420, 480, 540, 600]

    # The gap does not reset the 1m SMA3 count: at ts=240 the third 1m bar
    # (closes 100, 101, 103) completes the warmup.
    assert snaps[0].view_of("AAA").m1.sma3 is None
    assert snaps[1].view_of("AAA").m1.sma3 is None
    assert snaps[2].view_of("AAA").m1.sma3 == (100 + 101 + 103) / 3
    assert snaps[3].view_of("AAA").m1.sma3 == (101 + 103 + 104) / 3

    # The incomplete window [0, 300) is never published, and its partial
    # data must not leak into the next window.
    for snap in snaps[:8]:
        assert snap.view_of("AAA").m5.bar is None
    bar5 = snaps[8].view_of("AAA").m5.bar
    assert bar5 is not None
    assert (bar5.ts, bar5.open, bar5.close, bar5.volume) == (300, 105, 109, 10)


def test_window_not_completed_by_later_window_bars():
    # Four bars of window [0, 300), then a full window [300, 600). The first
    # window stays unpublished; no cross-window backfill.
    bars = [Bar("AAA", 60 * i, 100 + i, 100 + i, 100 + i, 100 + i, 1) for i in (0, 1, 2, 3)]
    bars += [Bar("AAA", 60 * i, 200 + i, 200 + i, 200 + i, 200 + i, 1) for i in range(5, 10)]
    _, snaps = run_collecting(bars)

    published_5m = [s.view_of("AAA").m5.bar for s in snaps]
    assert all(b is None for b in published_5m[:-1])
    final5 = published_5m[-1]
    assert final5.ts == 300
    assert final5.open == 205 and final5.close == 209
