from __future__ import annotations

import pytest

pytest.importorskip("pinelib")

from backtest_engine import BacktestConfig, BacktestEngine  # noqa: E402
from backtest_engine.execution_backends import (  # noqa: E402
    ExecutionBackend,
    PineRuntimeBackend,
    UnsupportedPineRuntimeBackendMode,
)
from backtest_engine.models import BarSeries  # noqa: E402


class GeneratedLikeIndicator:
    def __init__(self, params=None, runtime=None):
        self.params = params or {}
        self.rt = runtime

    def _process_bar(self, bar):
        self.rt.plot_recorder.record(
            bar_time=bar.time,
            bar_index=self.rt.bar_index + 1,
            name="plot",
            value=self.rt.close.current,
            title="Close",
            kwargs={},
        )


class GeneratedLikeStrategy(GeneratedLikeIndicator):
    pass


def _bars(n: int = 8) -> BarSeries:
    start = 1_700_000_000_000
    step = 900_000
    closes = [100.0 + i for i in range(n)]
    return BarSeries(
        time=[start + i * step for i in range(n)],
        open=[c for c in closes],
        high=[c + 1.0 for c in closes],
        low=[c - 1.0 for c in closes],
        close=closes,
        volume=[1.0 for _ in closes],
        time_close=[start + (i + 1) * step for i in range(n)],
    )


def _config(bars: BarSeries) -> BacktestConfig:
    return BacktestConfig(
        symbol="BTCUSDT",
        timeframe="15m",
        start_time=bars.time[0],
        end_time=bars.time[-1],
        score_start_time=bars.time[3],
        score_end_time=bars.time[-1],
        initial_capital=10_000.0,
        commission_type="percent",
        commission_value=0.0,
        default_qty_type="fixed",
        default_qty_value=1.0,
    )


def test_backend_protocol_and_pine_runtime_import_cleanly() -> None:
    assert ExecutionBackend is not None
    assert PineRuntimeBackend().name == "pine_runtime"


def test_pine_runtime_backend_runs_indicator_plot_handoff_only() -> None:
    bars = _bars()
    backend_result = PineRuntimeBackend().execute(
        GeneratedLikeIndicator,
        [bars.get_bar(i) for i in range(len(bars))],
        config=_config(bars),
        execution_window=None,
        is_indicator=True,
    )

    assert backend_result.trades == []
    assert backend_result.bar_results == []
    assert backend_result.plots is not None
    assert len(backend_result.plots) == len(bars)


def test_pine_runtime_backend_strategy_mode_fails_closed() -> None:
    bars = _bars()
    with pytest.raises(UnsupportedPineRuntimeBackendMode, match="make_generated_strategy_adapter"):
        BacktestEngine(_config(bars)).run(
            GeneratedLikeStrategy,
            bars=bars,
            effective_pre_bars=3,
            execution_backend=PineRuntimeBackend(),
        )


def test_pine_runtime_backend_string_strategy_mode_fails_closed() -> None:
    bars = _bars()
    with pytest.raises(UnsupportedPineRuntimeBackendMode, match="make_generated_strategy_adapter"):
        BacktestEngine(_config(bars)).run(
            GeneratedLikeStrategy,
            bars=bars,
            effective_pre_bars=3,
            execution_backend="pine_runtime",
        )
