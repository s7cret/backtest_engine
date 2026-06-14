from __future__ import annotations

from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.context import StrategyContext
from backtest_engine.core.realtime import BarTickSlice, RealtimeTickAttempt
from backtest_engine.models import Bar, Order, Position, Tick


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


def _order(order_id: str = "L") -> Order:
    return Order(
        id=order_id,
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


def test_guarded_realtime_tick_loop_rolls_back_each_attempt() -> None:
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    runtime = RuntimeWithState()
    strategy = StrategyWithState()
    engine.cash = 9000.0
    engine.position = Position(size=1.0, avg_price=100.0, direction="long")
    engine.orders = [_order()]
    tick_slice = BarTickSlice(
        bar_index=3,
        bar=Bar(time=100, open=10, high=10, low=10, close=10, time_close=160),
        ticks=(Tick(110, 10.5), Tick(120, 11.0)),
    )
    seen: list[tuple[int, float]] = []

    def mutate(tick: Tick, tick_index: int) -> None:
        seen.append((tick_index, tick.price))
        engine.cash = 8000.0 + tick_index
        engine.position.size = 9.0
        engine.orders[0].qty = 9.0
        runtime.value = 99
        strategy.flag = "after"

    attempts = engine._guarded_realtime_tick_loop_skeleton(
        tick_slice, ctx=ctx, strategy=strategy, runtime=runtime, on_attempt=mutate
    )

    assert seen == [(0, 10.5), (1, 11.0)]
    assert all(isinstance(a, RealtimeTickAttempt) for a in attempts)
    assert [a.tick_index for a in attempts] == [0, 1]
    assert [a.bar_index for a in attempts] == [3, 3]
    assert all(a.rolled_back for a in attempts)
    assert engine.cash == 9000.0
    assert engine.position.size == 1.0
    assert engine.orders[0].qty == 1.0
    assert runtime.value == 1
    assert strategy.flag == "before"
    assert ctx.state is engine.state


def test_guarded_realtime_tick_loop_empty_slice_is_noop() -> None:
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    tick_slice = BarTickSlice(
        bar_index=0,
        bar=Bar(time=100, open=10, high=10, low=10, close=10, time_close=160),
        ticks=(),
    )

    attempts = engine._guarded_realtime_tick_loop_skeleton(tick_slice, ctx=ctx)

    assert attempts == ()
