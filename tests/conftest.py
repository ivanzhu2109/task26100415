import pytest

from backtest import Bar


def make_bar(symbol, ts, open_, high=None, low=None, close=None, volume=1):
    """Bar helper: omitted h/l/c default to the open price."""
    return Bar(
        symbol=symbol,
        ts=ts,
        open=open_,
        high=high if high is not None else open_,
        low=low if low is not None else open_,
        close=close if close is not None else open_,
        volume=volume,
    )


@pytest.fixture
def bar():
    return make_bar
