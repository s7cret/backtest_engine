from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backtest_engine import BacktestConfig, BacktestEngine, Bar, Tick
from backtest_engine.context import StrategyContext
from backtest_engine.core.realtime import BarTickSlice
from backtest_engine.errors import ConfigError

WORKSPACE = Path(
    os.environ.get(
        "OPENCLAW_WORKSPACE",
        Path(__file__).resolve().parents[3] / "[workspace-root]" / "workspace",
    )
)
TRACE = WORKSPACE / "tv_strategy_oracle/realtime_probe/stage7j_to_9g_next50_2026-04-30/stage7n_guarded_tick_attempt_trace.json"


class NoopStrategy:
    def __init__(self, params, runtime, ctx):
        pass

    def _process_bar(self, bar, bar_index):
        pass


def _config(**kw) -> BacktestConfig:
    data = dict(symbol='BINANCE:BTCUSDT', timeframe='5', start_time=1777504200000, end_time=1777504500000, commission_type='none')
    data.update(kw)
    return BacktestConfig(**data)


def test_stage7i_boundary_trace_can_drive_guarded_skeleton_but_run_remains_fail_closed() -> None:
    if not TRACE.exists():
        pytest.skip(f"optional TradingView realtime trace not found: {TRACE}")
    trace = json.loads(TRACE.read_text(encoding='utf-8'))
    bar_payload = trace['bar']
    attempts_payload = trace['attempts']
    bar = Bar(**bar_payload)
    ticks = tuple(Tick(time=a['time'], price=a['price']) for a in attempts_payload)
    tick_slice = BarTickSlice(bar_index=0, bar=bar, ticks=ticks)

    engine = BacktestEngine(_config())
    ctx = StrategyContext(engine.config, engine.state)
    attempts = engine._guarded_realtime_tick_loop_skeleton(tick_slice, ctx=ctx)

    assert [a.tick.price for a in attempts] == [75959.31, 75987.62]
    assert all(a.rolled_back for a in attempts)
    assert all(not a.strategy_invoked for a in attempts)

    run_engine = BacktestEngine(_config(calc_on_every_tick=True, experimental_intrabar_strategy_mode=True, realtime_ticks=list(ticks)))
    with pytest.raises(ConfigError, match='tick replay is not implemented'):
        run_engine.run(NoopStrategy, bars=[bar])
