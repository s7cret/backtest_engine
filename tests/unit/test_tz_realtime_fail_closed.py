import pytest

from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.errors import ConfigError


class NoopStrategy:
    def __init__(self, params, runtime, ctx):
        pass

    def _process_bar(self, bar, bar_index):
        pass


def cfg(**kw):
    d = dict(symbol="S", timeframe="1D", start_time=1, end_time=1, commission_type="none")
    d.update(kw)
    return BacktestConfig(**d)


def test_calc_on_every_tick_fails_closed_in_parity_mode():
    engine = BacktestEngine(cfg(calc_on_every_tick=True))
    with pytest.raises(ConfigError, match="calc_on_every_tick"):
        engine.run(NoopStrategy, bars=[Bar(1, 10, 10, 10, 10)])


def test_calc_on_every_tick_requires_explicit_experimental_local_mode():
    result = BacktestEngine(
        cfg(calc_on_every_tick=True, experimental_intrabar_strategy_mode=True)
    ).run(NoopStrategy, bars=[Bar(1, 10, 10, 10, 10)])
    assert result.status == "completed"
