"""Batching / intra-timestamp ordering / future data must not change results."""

import random

from backtest import ReplayEngine
from conftest import make_bar

SYMS = ["A", "B", "C"]
BASE = {"A": 100, "B": 200, "C": 300}


def scenario_bars():
    bars = []
    for i in range(12):  # minutes 0..660
        ts = i * 60
        for j, sym in enumerate(SYMS):
            if sym == "B" and i == 5:
                continue  # missing minute
            if sym == "C" and i < 2:
                continue  # C starts late
            o = BASE[sym] + (i * 7 + j * 13) % 50
            bars.append(make_bar(sym, ts, o, high=o + 3, low=o - 4,
                                 close=o + (i % 3) - 1, volume=10 + i))
    bars.sort(key=lambda b: (b.ts, b.symbol))
    return bars


def strat(snap):
    out = {}
    for sym, frame in snap.symbols.items():
        if frame.sma3_1m is not None:
            out[sym] = 30 if frame.bar_1m.close > frame.sma3_1m else 10
    return out


def run(batches, cash=50_000):
    eng = ReplayEngine(cash, strat)
    for batch in batches:
        eng.add_bars(batch)
    eng.finish()
    return eng


def result_of(eng):
    return (eng.fills, eng.equity_curve, eng.cash, dict(eng.positions))


def test_batching_and_same_ts_order_do_not_matter():
    bars = scenario_bars()

    ref = result_of(run([bars]))

    # Split into batches at arbitrary points, incl. mid-timestamp splits.
    rng = random.Random(7)
    cuts = sorted(rng.sample(range(1, len(bars)), 5))
    batches, prev = [], 0
    for c in cuts + [len(bars)]:
        batches.append(bars[prev:c])
        prev = c
    assert result_of(run(batches)) == ref

    # Shuffle within each timestamp and use different batching.
    shuffled = []
    by_ts = {}
    for b in bars:
        by_ts.setdefault(b.ts, []).append(b)
    for ts in sorted(by_ts):
        group = by_ts[ts]
        rng.shuffle(group)
        shuffled.extend(group)
    mid = len(shuffled) // 2
    assert result_of(run([shuffled[:mid], shuffled[mid:]])) == ref


def test_ohlcv_beyond_open_does_not_change_fills():
    bars = scenario_bars()

    def fixed_strat(snap):
        return {sym: 10 for sym in snap.symbols}

    def run_fixed(bar_list):
        eng = ReplayEngine(1_000_000, fixed_strat)
        eng.add_bars(bar_list)
        eng.finish()
        return eng

    ref_fills = run_fixed(bars).fills
    assert ref_fills  # sanity: the scenario actually trades

    mutated = [
        make_bar(b.symbol, b.ts, b.open, high=b.high + 11, low=b.low - 13,
                 close=b.close + 7, volume=b.volume * 3)
        for b in bars
    ]
    assert run_fixed(mutated).fills == ref_fills


def test_future_data_does_not_change_completed_results():
    bars = scenario_bars()
    cut = next(i for i, b in enumerate(bars) if b.ts >= 360)
    head, tail = bars[:cut], bars[cut:]

    eng1 = run([head, tail])
    tail_mutated = [
        make_bar(b.symbol, b.ts, b.open + 25, close=b.close + 25) for b in tail
    ]
    eng2 = run([head, tail_mutated])

    # Everything completed at or before the last head timestamp is identical.
    horizon = head[-1].ts
    f1 = [f for f in eng1.fills if f.ts <= horizon]
    f2 = [f for f in eng2.fills if f.ts <= horizon]
    assert f1 == f2
    e1 = [p for p in eng1.equity_curve if p.ts <= horizon]
    e2 = [p for p in eng2.equity_curve if p.ts <= horizon]
    assert e1 == e2

    # Sanity: the mutated tail really did change later outcomes.
    assert eng1.equity_curve != eng2.equity_curve
