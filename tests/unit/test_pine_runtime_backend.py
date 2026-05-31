from __future__ import annotations

import sys

import pytest

PINELIB_REPO = "[local-home]/pinelib"
if PINELIB_REPO not in sys.path:
    sys.path.insert(0, PINELIB_REPO)
pytest.importorskip("pinelib")

from pinelib.strategy import StrategyContext  # noqa: E402

from backtest_engine import BacktestConfig, BacktestEngine  # noqa: E402
from backtest_engine.execution_backends import ExecutionBackend, PineRuntimeBackend  # noqa: E402
from backtest_engine.models import BarSeries  # noqa: E402


class GeneratedLikePineStrategy:
    def __init__(self, params=None, runtime=None):
        self.params = params or {}
        self.rt = runtime
        self.ctx = StrategyContext(
            initial_capital=10_000.0,
            commission_type="percent",
            commission_value=0.0,
            default_qty_type="fixed",
            default_qty_value=1.0,
            pyramiding=1,
        )
        self.ctx.attach_runtime(self.rt)

    def on_bar(self, runtime, strategy):
        idx = runtime.bar_index + 1
        close = runtime.close.current
        runtime.series("custom_state", "float").set_current(float(close) + idx)
        runtime.plot_recorder.record(
            bar_time=runtime.current_bar.time,
            bar_index=idx,
            name="plot",
            value=close,
            title="Close",
            kwargs={},
        )
        if idx == 1:
            strategy.entry("L", "long")
        if idx == 4:
            strategy.close_all()


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


@pytest.mark.skip(
    reason="legacy PineRuntimeBackend strategy-fill path; generated strategies must use BacktestEngine adapter"
)
def test_pine_runtime_backend_preserves_prehistory_and_plots() -> None:
    bars = _bars()
    result = BacktestEngine(_config(bars)).run(
        GeneratedLikePineStrategy,
        bars=bars,
        effective_pre_bars=3,
        execution_backend=PineRuntimeBackend(),
    )

    assert result.status == "completed"
    assert result.bars_processed == len(bars) - 3
    assert result.equity_curve is not None
    assert result.equity_curve[3].time == bars.time[3]
    assert result.score_net_profit == pytest.approx(2.0)
    assert result.plots is not None
    assert len(result.plots) == len(bars)


@pytest.mark.skip(
    reason="legacy PineRuntimeBackend strategy-fill path; generated strategies must use BacktestEngine adapter"
)
def test_pine_runtime_backend_phase_labels_closed_trade() -> None:
    bars = _bars()
    result = BacktestEngine(_config(bars)).run(
        GeneratedLikePineStrategy,
        bars=bars,
        effective_pre_bars=3,
        execution_backend="pine_runtime",
    )

    assert result.closed_trades is not None
    assert len(result.closed_trades) == 1
    trade = result.closed_trades[0]
    assert trade.profit_percent == pytest.approx(2.941176470588235)
    assert trade.max_runup == pytest.approx(4.0)
    assert trade.max_drawdown == pytest.approx(1.0)
    assert trade.mfe == pytest.approx(4.0)
    assert trade.mae == pytest.approx(-1.0)
    assert result.phase_trades is not None
    assert result.phase_trades[0].entry_phase == "prehistory"
    assert result.phase_trades[0].exit_phase == "score"
    assert result.phase_trades[0].crosses_score_boundary is True
