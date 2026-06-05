from dataclasses import fields
from pathlib import Path

import pytest

from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.core import (
    BacktestClock,
    EarlyStopChecker,
    ExecutionMode,
    JsonStateSerializer,
    PickleStateSerializer,
    activate_orders_for_bar,
    is_fast_mode,
)
from backtest_engine.core.state_snapshot import BrokerSnapshot
from backtest_engine.models import BarSeries, InstrumentModel, Order, Trade
from backtest_engine.ledger import trade_excursion_values
from backtest_engine.results import (
    calculate_drawdowns,
    equity_point,
    equity_values,
    max_drawdown,
    returns,
    sharpe_ratio,
    summarize_equity_curve,
    trades_to_rows,
)

BARS = [Bar(1, 10, 11, 9, 10), Bar(2, 12, 13, 11, 12), Bar(3, 14, 15, 13, 14)]


def cfg(**kw):
    d = dict(symbol="S", timeframe="1D", start_time=1, end_time=3, commission_type="none")
    d.update(kw)
    return BacktestConfig(**d)


class SerializableRuntime:
    def __init__(self):
        self.seen = []

    def begin_bar(self, bar, bar_index):
        self.seen.append(bar_index)

    def end_bar(self):
        pass

    def export_state(self):
        return {"seen": list(self.seen)}

    def restore_state(self, state):
        self.seen = list(state["seen"])


class SerializableStrategy:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx
        self.done = False

    def _process_bar(self, bar, bar_index):
        if not self.done:
            self.ctx.entry("L", "long", qty=1)
            self.done = True

    def export_state(self):
        return {"done": self.done}

    def restore_state(self, state):
        self.done = bool(state["done"])


def test_architecture_helpers_are_real():
    clock = BacktestClock()
    clock.advance(BARS[0], 0)
    assert clock.has_started and clock.time == 1
    assert is_fast_mode(ExecutionMode.FAST)
    decision = EarlyStopChecker(cfg(early_stop_enabled=True, min_equity_stop=9)).check(
        equity=8, drawdown_percent=0, bar_index=0, last_trade_bar=None
    )
    assert decision.should_stop and decision.reason == "min_equity_stop"
    order = Order("x", "entry", "long", "buy", "open", "market", 1, 0, 1, 1, "long")
    assert activate_orders_for_bar([order], 1) == [order]
    assert order.status == "active"


def test_backtest_config_has_no_market_data_ingress_fields():
    names = {field.name for field in fields(BacktestConfig)}

    assert "data_provider" not in names
    assert "preloaded_bars" not in names
    assert "provider" not in names


def test_engine_uses_canonical_timeframe_parser_for_parent_close():
    source = Path("backtest_engine/models/timeframe.py").read_text(encoding="utf-8")

    assert "parse_timeframe" in source
    assert ".endswith(" not in source
    assert ".isdigit(" not in source


def test_trade_excursion_math_lives_in_ledger_module():
    source = Path("backtest_engine/core/engine.py").read_text(encoding="utf-8")

    assert "def trade_excursion_values" not in source
    assert "trade_excursion_values" in source
    trade = Trade(
        id="t1",
        entry_id="t1",
        exit_id=None,
        direction="long",
        entry_time=1,
        entry_bar_index=0,
        entry_price=100,
        exit_time=None,
        exit_bar_index=None,
        exit_price=None,
        qty=1,
        commission_entry=0,
        commission_exit=0,
        profit=0,
        profit_percent=0,
        is_open=True,
    )
    mfe, mae, max_runup, max_drawdown = trade_excursion_values(
        trade,
        Bar(2, 100, 110, 95, 105),
        InstrumentModel(),
    )

    assert mfe == 10
    assert mae == -5
    assert max_runup == 10
    assert max_drawdown == 5


def test_equity_curve_math_lives_in_results_module():
    source = Path("backtest_engine/core/engine.py").read_text(encoding="utf-8")

    assert "drawdown = max(0.0, peak - equity)" not in source
    point = equity_point(
        bar_index=0,
        time=1,
        equity=90,
        cash=90,
        position_size=0,
        position_avg_price=None,
        open_profit=0,
        realized_profit=-10,
        peak=100,
        trough=90,
    )
    summary = summarize_equity_curve([point], default_equity=100)

    assert point.drawdown == 10
    assert point.drawdown_percent == 10
    assert summary.final_equity == 90
    assert summary.max_drawdown == 10


def test_engine_slice_preserves_bar_close_times():
    engine = BacktestEngine(cfg())
    sliced = engine._slice_range(
        BarSeries.from_bars(
            [
                Bar(1, 10, 11, 9, 10, time_close=2),
                Bar(2, 12, 13, 11, 12, time_close=3),
                Bar(3, 14, 15, 13, 14, time_close=4),
            ]
        )
    )

    assert list(sliced.time_close or []) == [2, 3, 4]


def test_strategy_context_has_no_notimplemented_risk_methods():
    source = Path("backtest_engine/context/strategy_context.py").read_text(encoding="utf-8")

    assert "NotImplementedError" not in source
    assert "UnsupportedRiskRuleError" in source


def test_result_helpers_are_public_functions():
    dds = calculate_drawdowns([100, 90, 110, 80])
    assert dds[1].drawdown == 10
    assert max_drawdown([100, 90, 110, 80]).drawdown == 30
    r = BacktestEngine(cfg()).run(SerializableStrategy, bars=BARS)
    assert equity_values(r.equity_curve)
    assert returns(r.equity_curve)
    assert sharpe_ratio([0.01, 0.02, -0.01]) is not None
    assert trades_to_rows(r.open_trades)[0]["entry_id"] == "L"


def test_resume_state_export_and_restore_extension_point():
    runtime = SerializableRuntime()
    first = BacktestEngine(cfg(export_resume_state=True, runtime=runtime)).run(
        SerializableStrategy, bars=BARS[:2]
    )
    assert first.resume_state is not None
    assert isinstance(first.resume_state.broker_state, BrokerSnapshot)
    second_runtime = SerializableRuntime()
    second = BacktestEngine(cfg(start_time=1, end_time=3, runtime=second_runtime)).run(
        SerializableStrategy, bars=BARS, resume_state=first.resume_state
    )
    assert second.bars_processed == 3
    assert second.open_trades[0].entry_id == "L"
    assert second_runtime.seen[-1] == 2


def test_resume_requires_broker_snapshot_not_bare_stub():
    state = (
        BacktestEngine(cfg(export_resume_state=True))
        .run(SerializableStrategy, bars=BARS[:1])
        .resume_state
    )
    assert state is not None
    broken = state.__class__(
        bar_index=state.bar_index, config_snapshot_hash=state.config_snapshot_hash
    )
    with pytest.raises(Exception, match="broker_state"):
        BacktestEngine(cfg()).run(SerializableStrategy, bars=BARS, resume_state=broken)


def test_json_state_serializer_round_trip_primitives():
    serializer = JsonStateSerializer()
    payload = serializer.dumps({"a": [1, 2]})
    assert serializer.loads(payload) == {"a": [1, 2]}


def test_pickle_state_serializer_requires_trusted_opt_in():
    payload = PickleStateSerializer(allow_loads=True).dumps({"a": 1})

    with pytest.raises(ValueError, match="trusted local snapshots"):
        PickleStateSerializer(allow_loads=False).loads(payload)

    assert PickleStateSerializer(allow_loads=True).loads(payload) == {"a": 1}
