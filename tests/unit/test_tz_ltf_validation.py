import pytest

from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.errors import BarMagnifierUnavailableError


class Provider:
    def __init__(self, bars):
        self.bars = bars

    def get_lower_tf_bars(self, *args):
        return self.bars


class Noop:
    def __init__(self, params, runtime, ctx):
        pass

    def _process_bar(self, bar, bar_index):
        pass


def cfg(lower):
    return BacktestConfig(
        symbol="S",
        timeframe="60",
        start_time=0,
        end_time=3600,
        commission_type="none",
        use_bar_magnifier=True,
        bar_magnifier_lower_tf="15",
        data_provider=Provider(lower),
    )


def run(lower):
    return BacktestEngine(cfg(lower)).run(Noop, bars=[Bar(0, 10, 12, 8, 11, time_close=3600)])


def valid_lower():
    return [
        Bar(0, 10, 11, 9, 10, time_close=900),
        Bar(900, 10, 12, 10, 11, time_close=1800),
        Bar(1800, 11, 11, 8, 9, time_close=2700),
        Bar(2700, 9, 11, 9, 11, time_close=3600),
    ]


def test_valid_ltf_data_is_accepted():
    result = run(valid_lower())
    assert result.total_trades == 0


@pytest.mark.parametrize(
    "bars, message",
    [
        ([valid_lower()[1], valid_lower()[0]], "not sorted"),
        ([valid_lower()[0], valid_lower()[0]], "duplicate"),
        ([Bar(-1, 10, 10, 10, 10, time_close=1)], "outside parent"),
        ([Bar(0, 10, 10, 10, 10)], "missing time_close"),
        ([Bar(0, 10, 10, 10, 10, time_close=0)], "invalid/open time_close"),
        ([Bar(3500, 10, 10, 10, 10, time_close=3700)], "closes outside"),
    ],
)
def test_invalid_ltf_data_fails_closed(bars, message):
    with pytest.raises(BarMagnifierUnavailableError, match=message):
        run(bars)
