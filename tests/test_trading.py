"""Hand-computed trading semantics: cash limits, sell-funds-buy, overrides."""

from backtest import Fill, ReplayEngine
from conftest import make_bar


def replay(bars, strat, cash):
    eng = ReplayEngine(cash, strat)
    eng.add_bars(bars)
    eng.finish()
    return eng


def test_buy_limited_by_cash_and_remainder_cancelled():
    bars = [make_bar("A", t, 100) for t in (0, 60, 120, 180)]

    def strat(snap):
        return {"A": 15} if snap.ts == 60 else {}

    eng = replay(bars, strat, cash=1000)
    # 1000 // 100 = 10 shares; the unfilled 5 are cancelled, not retried.
    assert eng.fills == [Fill(60, "A", "buy", 10, 100)]
    assert eng.cash == 0
    assert dict(eng.positions) == {"A": 10}


def test_same_timestamp_sell_releases_cash_for_buy():
    bars = [make_bar(s, t, 100) for t in (0, 60, 120, 180) for s in ("A", "B")]

    def strat(snap):
        if snap.ts == 60:
            return {"A": 10}
        if snap.ts == 120:
            return {"A": 0, "B": 10}
        return {}

    eng = replay(bars, strat, cash=1000)
    assert eng.fills == [
        Fill(60, "A", "buy", 10, 100),    # cash 1000 -> 0
        Fill(120, "A", "sell", 10, 100),  # cash 0 -> 1000 (sells first)
        Fill(120, "B", "buy", 10, 100),   # cash 1000 -> 0
    ]
    assert eng.cash == 0
    assert dict(eng.positions) == {"B": 10}
    # Equity at t=120 is recorded before the open fills: still holding A.
    point = [p for p in eng.equity_curve if p.ts == 120][0]
    assert point.cash == 0
    assert dict(point.positions) == {"A": 10}
    assert point.equity == 1000


def test_waiting_target_is_overridden_by_newer_target():
    bars = [make_bar("A", t, 100) for t in range(0, 360, 60)]
    bars += [make_bar("B", 0, 50), make_bar("B", 240, 50), make_bar("B", 300, 50)]
    bars.sort(key=lambda b: b.ts)

    def strat(snap):
        if snap.ts == 60:
            return {"B": 5}   # B has no open until t=240 -> target waits
        if snap.ts == 120:
            return {"B": 9}   # overrides the still-pending target
        return {}

    eng = replay(bars, strat, cash=1000)
    assert eng.fills == [Fill(240, "B", "buy", 9, 50)]
    assert eng.cash == 1000 - 9 * 50
    assert dict(eng.positions) == {"B": 9}


def test_fill_uses_opening_bar_not_just_closed_bar():
    bars = [make_bar("A", 0, 100), make_bar("A", 60, 999), make_bar("A", 120, 5)]

    def strat(snap):
        return {"A": 1} if snap.ts == 60 else {}

    eng = replay(bars, strat, cash=10_000)
    # Decision at t=60 fills at the bar opening at t=60 (price 999),
    # never at the just-closed bar's open (100).
    assert eng.fills == [Fill(60, "A", "buy", 1, 999)]


def test_target_without_future_open_never_fills():
    bars = [make_bar("A", t, 100) for t in (0, 60)]

    def strat(snap):
        return {"A": 5}  # decided at every close, incl. the last one

    eng = replay(bars, strat, cash=10_000)
    # Target from the final close (t=120) has no later open; finish() must
    # not force a fill. Only the t=60 open executes.
    assert eng.fills == [Fill(60, "A", "buy", 5, 100)]
    assert eng.cash == 10_000 - 500
