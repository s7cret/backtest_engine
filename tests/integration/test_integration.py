from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.batch import BatchBacktestRunner, BacktestJob

B = [
    Bar(1, 10, 11, 9, 10),
    Bar(2, 10, 12, 9, 11),
    Bar(3, 11, 12, 8, 9),
    Bar(4, 9, 10, 7, 8),
]


class Bracket:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 2:
            self.ctx.exit("X", limit=12, stop=8)


class Rev:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 1:
            self.ctx.entry("S", "short", qty=1)


def cfg():
    return BacktestConfig(
        symbol="S", timeframe="1D", start_time=1, end_time=4, commission_type="none"
    )


def test_bracket_and_reversal_and_batch():
    assert BacktestEngine(cfg()).run(Bracket, bars=B).closed_trades
    assert BacktestEngine(cfg()).run(Rev, bars=B).closed_trades
    out = BatchBacktestRunner(cfg()).run(
        [BacktestJob("a", Bracket, bars=B), BacktestJob("b", Rev, bars=B)]
    )
    assert set(out) == {"a", "b"}
