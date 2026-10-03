"""Determinism and no-lookahead guarantees."""

import random

import pytest

from backtest import Bar, ReplayEngine

SYMBOLS = ["AAA", "BBB", "CCC"]


def make_bars(minutes=30, seed=7):
    rng = random.Random(seed)
    bars = []
    for i in range(minutes):
        for sym in SYMBOLS:
            if rng.random() < 0.1:  # random missing minutes
                continue
            base = 500 + 10 * i + hash(sym) % 50
            o = base + rng.randint(-5, 5)
            c = o + rng.randint(-8, 8)
            h = max(o, c) + rng.randint(0, 6)
            l = min(o, c) - rng.randint(0, 6)
            v = rng.randint(1, 100)
            bars.append(Bar(sym, 60 * i, o, h, l, c, v))
    return bars


def make_strategy(seed=99):
    rng = random.Random(seed)
    scripted = {}

    def strategy(snapshot):
        targets = {}
        for view in snapshot.symbols:
            key = (snapshot.ts, view.symbol)
            if key not in scripted:
                scripted[key] = rng.randint(0, 4)
            targets[view.symbol] = scripted[key]
        return targets

    return strategy


def run(bars, batcher=None, seed=99):
    snapshots = []
    base_strategy = make_strategy(seed)

    def strategy(snapshot):
        snapshots.append(snapshot)
        return base_strategy(snapshot)

    engine = ReplayEngine(strategy, 50_000)
    if batcher is None:
        engine.add_bars(bars)
    else:
        for batch in batcher(bars):
            engine.add_bars(batch)
    engine.finish()
    return engine, snapshots


def snapshot_key(snapshot):
    return (
        snapshot.ts,
        snapshot.cash,
        snapshot.positions,
        tuple(
            (v.symbol, v.m1.bar, v.m1.sma3, v.m5.bar, v.m5.sma3)
            for v in snapshot.symbols
        ),
    )


def result_key(engine, snapshots):
    return (
        engine.fills,
        engine.equity_curve,
        engine.cash,
        engine.positions,
        [snapshot_key(s) for s in snapshots],
    )


def test_batching_and_same_timestamp_order_are_irrelevant():
    bars = make_bars()
    reference = result_key(*run(bars))

    rng = random.Random(1234)
    for _ in range(5):
        # Shuffle within each timestamp group, then split into random
        # batches (batch boundaries may fall inside a timestamp group).
        shuffled = []
        by_ts = {}
        for bar in bars:
            by_ts.setdefault(bar.ts, []).append(bar)
        for ts in sorted(by_ts):
            group = by_ts[ts]
            rng.shuffle(group)
            shuffled.extend(group)

        def batcher(seq, rng=rng):
            i = 0
            while i < len(seq):
                n = rng.randint(1, 17)
                yield seq[i : i + n]
                i += n

        assert result_key(*run(shuffled, batcher)) == reference


def test_opening_fill_depends_only_on_open_price():
    bars = make_bars()
    engine_ref, _ = run(bars)

    # Pick a timestamp with at least one fill and rewrite the high/low/
    # close/volume of every bar opening at that timestamp.
    fill_ts = engine_ref.fills[-1].ts
    mutated = [
        Bar(b.symbol, b.ts, b.open, b.open + 999, 1, b.open + 123, 999_999)
        if b.ts == fill_ts
        else b
        for b in bars
    ]
    engine_mut, _ = run(mutated)

    before = [f for f in engine_ref.fills if f.ts <= fill_ts]
    after = [f for f in engine_mut.fills if f.ts <= fill_ts]
    assert before == after
    assert any(f.ts == fill_ts for f in after)


def test_future_data_does_not_change_past_results():
    bars = make_bars()
    engine_ref, snaps_ref = run(bars)

    cutoff = engine_ref.equity_curve[len(engine_ref.equity_curve) // 2].ts
    mutated = [
        Bar(b.symbol, b.ts, b.open + 777, b.high + 777, b.low + 777, b.close + 777, b.volume)
        if b.ts > cutoff
        else b
        for b in bars
    ]
    engine_mut, snaps_mut = run(mutated)

    ref_equity = [p for p in engine_ref.equity_curve if p.ts <= cutoff]
    mut_equity = [p for p in engine_mut.equity_curve if p.ts <= cutoff]
    assert ref_equity == mut_equity

    ref_fills = [f for f in engine_ref.fills if f.ts <= cutoff]
    mut_fills = [f for f in engine_mut.fills if f.ts <= cutoff]
    assert ref_fills == mut_fills

    ref_snaps = [snapshot_key(s) for s in snaps_ref if s.ts <= cutoff]
    mut_snaps = [snapshot_key(s) for s in snaps_mut if s.ts <= cutoff]
    assert ref_snaps == mut_snaps


def test_input_validation():
    hold = lambda s: {v.symbol: 0 for v in s.symbols}

    engine = ReplayEngine(hold, 100)
    with pytest.raises(ValueError):
        engine.add_bars([Bar("AAA", 61, 1, 1, 1, 1, 1)])  # not minute-aligned

    engine = ReplayEngine(hold, 100)
    engine.add_bars([Bar("AAA", 120, 1, 1, 1, 1, 1)])
    with pytest.raises(ValueError):
        engine.add_bars([Bar("AAA", 60, 1, 1, 1, 1, 1)])  # decreasing ts
    with pytest.raises(ValueError):
        engine.add_bars([Bar("AAA", 120, 1, 1, 1, 1, 1)])  # duplicate

    engine.finish()
    with pytest.raises(RuntimeError):
        engine.add_bars([Bar("AAA", 180, 1, 1, 1, 1, 1)])
    with pytest.raises(RuntimeError):
        engine.finish()

    with pytest.raises(ValueError):
        ReplayEngine(lambda s: {}, -1)


def test_strategy_output_validation():
    bars = [Bar("AAA", 0, 1, 1, 1, 1, 1), Bar("AAA", 60, 1, 1, 1, 1, 1)]

    engine = ReplayEngine(lambda s: {}, 100)  # missing symbol
    engine.add_bars(bars)
    with pytest.raises(ValueError):
        engine.finish()

    engine = ReplayEngine(lambda s: {"AAA": -1}, 100)  # negative target
    engine.add_bars(bars)
    with pytest.raises(ValueError):
        engine.finish()

    engine = ReplayEngine(lambda s: {"AAA": 1, "ZZZ": 0}, 100)  # extra symbol
    engine.add_bars(bars)
    with pytest.raises(ValueError):
        engine.finish()
