from dataclasses import replace

import pytest
from openpine_contracts import ExecutionEvent

from backtest_engine import BacktestCallbacks, BacktestEngine
from backtest_engine.errors import StrategyRuntimeError
from tests.unit.test_engine_projection import _bars, _plain_config


class CallbackStrategy:
    required_runtime_capabilities = ()

    def __init__(self, params, runtime, ctx):
        self.ctx = ctx
        self.events = []

    def run_bar(self, *_):
        raise AssertionError("typed execution path must be preferred")

    def run_callback(self, bar, event):
        assert isinstance(event, ExecutionEvent)
        self.events.append(event)
        if event.bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if event.bar_index == 1:
            self.ctx.close("L")


def test_engine_emits_causal_fill_phase_and_keeps_normal_close_distinct():
    engine = BacktestEngine(
        replace(_plain_config(), calc_on_order_fills=True, force_close_on_end=False)
    )
    output = []
    result = engine.run(
        CallbackStrategy,
        bars=_bars(),
        callbacks=BacktestCallbacks(
            on_strategy_callback=lambda row: output.append(row["execution_event"])
        ),
    )
    assert result.status == "completed"
    events = engine.last_strategy.events
    assert [event.sequence for event in events] == list(range(len(events)))
    assert [event.to_dict() for event in events] == output
    assert all(event.last_bar_index == 3 and event.tick_index == 0 for event in events)
    repeated = [event for event in events if event.bar_index == 1]
    assert [(event.phase, event.recalc_iteration) for event in repeated] == [
        ("ORDER_FILL_RECALC", 0),
        ("ORDER_FILL_RECALC", 1),
        ("HISTORICAL_EVAL", 2),
    ]
    assert repeated[0].fill_order_id == "L"
    assert repeated[0].fill_price == "11"
    assert repeated[-1].fill_order_id is None
    assert [event.bar_index for event in events if event.is_last] == [3]
    assert [event.bar_index for event in events if event.is_last_confirmed_history] == [3]


def test_reusing_engine_resets_execution_sequence():
    engine = BacktestEngine(replace(_plain_config(), calc_on_order_fills=True))
    snapshots = []
    for _ in range(2):
        engine.run(CallbackStrategy, bars=_bars())
        snapshots.append([event.to_dict() for event in engine.last_strategy.events])
    assert snapshots[0] == snapshots[1]


class EndlessRecalculation:
    required_runtime_capabilities = ()

    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def run_bar(self, bar, index):
        if self.ctx.state.position_size == 0:
            self.ctx.entry("L", "long", qty=1)
        else:
            self.ctx.close("L", qty=1, immediately=True)


def test_recalculation_budget_exhaustion_is_an_error_not_partial_success():
    engine = BacktestEngine(replace(_plain_config(), calc_on_order_fills=True, max_recalc_depth=3))
    with pytest.raises(StrategyRuntimeError, match="MAX_RECALC_DEPTH_REACHED"):
        engine.run(EndlessRecalculation, bars=_bars())
    assert engine.errors[-1].code == "MAX_RECALC_DEPTH_REACHED"
