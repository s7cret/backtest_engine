from __future__ import annotations

import pytest

from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.context import StrategyContext
from backtest_engine.core.state_snapshot import RealtimeExecutionCheckpoint
from backtest_engine.errors import ResumeUnsupportedError
from backtest_engine.models import Order, Position


class RuntimeWithState:
    def __init__(self) -> None:
        self.value = 1

    def export_state(self) -> dict[str, int]:
        return {"value": self.value}

    def restore_state(self, state: object) -> None:
        assert isinstance(state, dict)
        self.value = int(state["value"])


class StrategyWithState:
    def __init__(self) -> None:
        self.flag = "before"

    def export_state(self) -> dict[str, str]:
        return {"flag": self.flag}

    def restore_state(self, state: object) -> None:
        assert isinstance(state, dict)
        self.flag = str(state["flag"])


def _engine() -> BacktestEngine:
    return BacktestEngine(
        BacktestConfig(
            symbol="TEST",
            timeframe="1",
            start_time=0,
            end_time=10,
            commission_type="none",
        )
    )


def _order() -> Order:
    return Order(
        id="L",
        kind="entry",
        direction="long",
        side="buy",
        position_effect="open",
        order_type="market",
        qty=1.0,
        created_bar_index=0,
        created_time=0,
        active_from_bar_index=1,
    )


def test_realtime_execution_checkpoint_restores_broker_runtime_and_strategy() -> None:
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    runtime = RuntimeWithState()
    strategy = StrategyWithState()
    engine.cash = 9000.0
    engine.position = Position(size=1.0, avg_price=100.0, direction="long")
    engine.orders = [_order()]
    checkpoint = engine._export_realtime_execution_checkpoint(
        strategy=strategy, runtime=runtime
    )

    engine.cash = 8000.0
    engine.position.size = 5.0
    engine.orders[0].qty = 5.0
    runtime.value = 99
    strategy.flag = "after"

    engine._restore_realtime_execution_checkpoint(
        checkpoint, ctx=ctx, strategy=strategy, runtime=runtime
    )

    assert isinstance(checkpoint, RealtimeExecutionCheckpoint)
    assert engine.cash == 9000.0
    assert engine.position.size == 1.0
    assert engine.orders[0].qty == 1.0
    assert runtime.value == 1
    assert strategy.flag == "before"
    assert ctx.state is engine.state


def test_realtime_execution_checkpoint_is_detached_from_exported_state_mutations() -> (
    None
):
    engine = _engine()
    runtime = RuntimeWithState()
    strategy = StrategyWithState()
    checkpoint = engine._export_realtime_execution_checkpoint(
        strategy=strategy, runtime=runtime
    )

    assert isinstance(checkpoint.runtime_state, dict)
    assert isinstance(checkpoint.strategy_state, dict)
    checkpoint.runtime_state["value"] = 42  # type: ignore[index]
    checkpoint.strategy_state["flag"] = "mutated"  # type: ignore[index]
    runtime.value = 7
    strategy.flag = "later"

    engine._restore_realtime_execution_checkpoint(
        checkpoint, strategy=strategy, runtime=runtime
    )

    assert runtime.value == 42
    assert strategy.flag == "mutated"
    checkpoint.runtime_state["value"] = 100  # type: ignore[index]
    assert runtime.value == 42


def test_realtime_execution_checkpoint_requires_restore_capability_when_state_present() -> (
    None
):
    engine = _engine()
    checkpoint = engine._export_realtime_execution_checkpoint(
        runtime=RuntimeWithState()
    )

    with pytest.raises(ResumeUnsupportedError, match="runtime_state"):
        engine._restore_realtime_execution_checkpoint(checkpoint, runtime=object())


def test_realtime_execution_checkpoint_rejects_wrong_checkpoint_type() -> None:
    engine = _engine()
    with pytest.raises(ResumeUnsupportedError, match="RealtimeExecutionCheckpoint"):
        engine._restore_realtime_execution_checkpoint(object())  # type: ignore[arg-type]
