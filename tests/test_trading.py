"""Hand-computable checks: fills, cash constraints, target lifecycle."""

from backtest import Bar, ReplayEngine


def flat(symbol, ts, price, volume=1):
    return Bar(symbol, ts, price, price, price, price, volume)


def test_insufficient_cash_partial_buy_and_remainder_cancelled():
    # Cash 150, price 100: target 2 -> only 1 share fills, rest cancelled.
    calls = []

    def strategy(snapshot):
        calls.append(snapshot)
        if len(calls) == 1:
            return {"AAA": 2}
        # Afterwards just hold whatever we have.
        return {"AAA": snapshot.position_of("AAA")}

    bars = [flat("AAA", 60 * i, 100) for i in range(4)]
    engine = ReplayEngine(strategy, 150)
    engine.add_bars(bars)
    engine.finish()

    # Decision at ts=60 -> executed at the opening phase of ts=60 using the
    # open of the bar that opens at 60 (never the just-closed bar open).
    assert engine.fills == [type(engine.fills[0])(60, "AAA", "buy", 1, 100)]
    assert engine.cash == 50
    assert engine.positions == {"AAA": 1}


def test_unfilled_remainder_does_not_linger():
    calls = []

    def strategy(snapshot):
        calls.append(snapshot)
        if len(calls) == 1:
            return {"AAA": 2}
        return {"AAA": snapshot.position_of("AAA")}

    bars = [
        flat("AAA", 0, 100),
        flat("AAA", 60, 100),
        flat("AAA", 120, 40),  # cheap open: a lingering target would fill here
        flat("AAA", 180, 40),
    ]
    engine = ReplayEngine(strategy, 150)
    engine.add_bars(bars)
    engine.finish()

    assert [(f.ts, f.side, f.qty, f.price) for f in engine.fills] == [
        (60, "buy", 1, 100)
    ]
    assert engine.cash == 50
    assert engine.positions == {"AAA": 1}


def test_same_timestamp_sells_fund_buys_in_name_order():
    # AAA and BBB both open at 100. Buy 1 AAA, then reverse the position:
    # the AAA sale must release cash for the BBB purchase at the same tick.
    script = [
        {"AAA": 1, "BBB": 0},
        {"AAA": 0, "BBB": 2},
    ]
    calls = []

    def strategy(snapshot):
        idx = min(len(calls), len(script) - 1)
        calls.append(snapshot)
        return script[idx]

    bars = []
    for i in range(4):
        bars.append(flat("AAA", 60 * i, 100))
        bars.append(flat("BBB", 60 * i, 50))
    engine = ReplayEngine(strategy, 100)
    engine.add_bars(bars)
    engine.finish()

    got = [(f.ts, f.symbol, f.side, f.qty, f.price) for f in engine.fills]
    assert got == [
        (60, "AAA", "buy", 1, 100),
        (120, "AAA", "sell", 1, 100),  # sells execute before buys
        (120, "BBB", "buy", 2, 50),  # funded by the same-tick AAA sale
    ]
    assert engine.cash == 0
    assert engine.positions == {"BBB": 2}


def test_waiting_target_is_overwritten_by_newer_decision():
    # AAA trades only at t=0 and t=300; BBB trades every minute so decisions
    # keep happening. The AAA target set at ts=60 is replaced at ts=120 and
    # only the newest one executes when AAA finally opens at t=300.
    script = [
        {"AAA": 5, "BBB": 0},
        {"AAA": 2, "BBB": 0},
    ]
    calls = []

    def strategy(snapshot):
        idx = min(len(calls), len(script) - 1)
        calls.append(snapshot)
        return dict(script[idx])

    bars = [flat("AAA", 0, 50), flat("AAA", 300, 50)]
    bars += [flat("BBB", 60 * i, 10) for i in range(6)]
    bars.sort(key=lambda b: (b.ts, b.symbol))
    engine = ReplayEngine(strategy, 1000)
    engine.add_bars(bars)
    engine.finish()

    got = [(f.ts, f.symbol, f.side, f.qty, f.price) for f in engine.fills]
    assert got == [(300, "AAA", "buy", 2, 50)]
    assert engine.positions == {"AAA": 2}
    assert engine.cash == 900


def test_equity_recorded_before_opening_fills():
    calls = []

    def strategy(snapshot):
        calls.append(snapshot)
        return {"AAA": 1}

    bars = [flat("AAA", 0, 100), flat("AAA", 60, 110)]
    engine = ReplayEngine(strategy, 1000)
    engine.add_bars(bars)
    engine.finish()

    curve = engine.equity_curve
    # ts=60: close of the first bar, before the buy at the ts=60 opening.
    assert (curve[0].ts, curve[0].cash, curve[0].positions, curve[0].equity) == (
        60, 1000, (), 1000,
    )
    # ts=120: position valued at the last published 1m close (110).
    assert (curve[1].ts, curve[1].cash, curve[1].positions, curve[1].equity) == (
        120, 890, (("AAA", 1),), 1000,
    )
