from __future__ import annotations

from backtest_engine import BacktestConfig, BacktestEngine, Bar
from openpine_contracts import Finality


class RunBarOnlyStrategy:
    calls: list[tuple[int, int]] = []

    def __init__(self, params: dict, runtime: object, ctx: object) -> None:
        del params, runtime
        self.ctx = ctx

    def run_bar(self, bar: Bar, bar_index: int) -> None:
        self.calls.append((bar.time, bar_index))


def test_native_engine_accepts_rc6_run_bar_driver_without_process_bar() -> None:
    RunBarOnlyStrategy.calls = []
    config = BacktestConfig(symbol="S", timeframe="1m", start_time=0, end_time=0)
    bar = Bar(
        time=0,
        time_close=59_999,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
        finality=Finality.FINAL,
    )

    result = BacktestEngine(config).run(RunBarOnlyStrategy, bars=[bar])

    assert result.status == "completed"
    assert RunBarOnlyStrategy.calls == [(0, 0)]
    assert not hasattr(RunBarOnlyStrategy, "_process_bar")
