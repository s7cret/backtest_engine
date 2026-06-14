from __future__ import annotations

from dataclasses import dataclass

import pytest

from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.context import StrategyContext
from backtest_engine.core.realtime import (
    BarTickSlice,
    RealtimeTickCommitPolicy,
    RuntimeTickUpdate,
)
from backtest_engine.errors import ConfigError
from backtest_engine.models import Bar, Position, Tick


@dataclass
class RuntimeWithRealtimeTicks:
    value: int = 1
    seen: list[RuntimeTickUpdate] | None = None

    def __post_init__(self) -> None:
        if self.seen is None:
            self.seen = []

    def export_state(self, *, include_varip: bool = True) -> dict[str, object]:
        state = {"value": self.value, "seen": list(self.seen or [])}
        if include_varip:
            state["varip"] = "captured"
        return state

    def restore_state(self, state: object) -> None:
        assert isinstance(state, dict)
        self.value = int(state["value"])
        self.seen = list(state["seen"])

    def update_realtime_tick(self, tick: RuntimeTickUpdate) -> Bar:
        assert self.seen is not None
        self.seen.append(tick)
        self.value += 1
        return Bar(
            time=tick.time or 0,
            open=10,
            high=tick.price,
            low=10,
            close=tick.price,
            volume=tick.volume,
        )


class StrategyRecordsTickBars:
    def __init__(self) -> None:
        self.seen_closes: list[float] = []
        self.flag = "before"

    def export_state(self) -> dict[str, object]:
        return {"seen_closes": list(self.seen_closes), "flag": self.flag}

    def restore_state(self, state: object) -> None:
        assert isinstance(state, dict)
        self.seen_closes = list(state["seen_closes"])
        self.flag = str(state["flag"])

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        del bar_index
        self.seen_closes.append(bar.close)
        self.flag = "after"


class StrategyAddsBufferedOrder(StrategyRecordsTickBars):
    def __init__(self, ctx: StrategyContext) -> None:
        super().__init__()
        self.ctx = ctx

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        super()._process_bar(bar, bar_index)
        self.ctx.entry("L", "long", qty=1)


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


def _slice() -> BarTickSlice:
    return BarTickSlice(
        bar_index=3,
        bar=Bar(time=100, open=10, high=10, low=10, close=10, time_close=160),
        ticks=(Tick(110, 10.5, volume=2), Tick(120, 11.0, volume=3)),
    )


def test_guarded_strategy_tick_invocation_rolls_back_runtime_strategy_and_broker() -> (
    None
):
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    runtime = RuntimeWithRealtimeTicks()
    strategy = StrategyRecordsTickBars()
    engine.cash = 9000.0
    engine.position = Position(size=1.0, avg_price=100.0, direction="long")

    attempts = engine._guarded_realtime_strategy_tick_loop_skeleton(
        _slice(), ctx=ctx, strategy=strategy, runtime=runtime
    )

    assert [a.tick.price for a in attempts] == [10.5, 11.0]
    assert all(a.rolled_back and a.strategy_invoked for a in attempts)
    assert runtime.value == 1
    assert runtime.seen == []
    assert strategy.seen_closes == []
    assert strategy.flag == "before"
    assert engine.cash == 9000.0
    assert engine.position.size == 1.0


def test_guarded_strategy_tick_invocation_restores_command_buffer() -> None:
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    ctx.entry("PRE", "long", qty=2)
    runtime = RuntimeWithRealtimeTicks()
    strategy = StrategyAddsBufferedOrder(ctx)

    engine._guarded_realtime_strategy_tick_loop_skeleton(
        _slice(), ctx=ctx, strategy=strategy, runtime=runtime
    )

    assert len(ctx.buffer.commands) == 1
    assert ctx.buffer.commands[0].kwargs["id"] == "PRE"


def test_guarded_strategy_tick_invocation_without_runtime_update_uses_parent_bar() -> (
    None
):
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    strategy = StrategyRecordsTickBars()

    engine._guarded_realtime_strategy_tick_loop_skeleton(
        _slice(), ctx=ctx, strategy=strategy
    )

    assert strategy.seen_closes == []
    assert strategy.flag == "before"


def test_guarded_strategy_tick_invocation_can_commit_only_final_tick_in_skeleton() -> (
    None
):
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    runtime = RuntimeWithRealtimeTicks()
    strategy = StrategyRecordsTickBars()

    attempts = engine._guarded_realtime_strategy_tick_loop_skeleton(
        _slice(),
        ctx=ctx,
        strategy=strategy,
        runtime=runtime,
        commit_policy=RealtimeTickCommitPolicy(commit_final_tick=True),
    )

    assert [a.policy for a in attempts] == ["discard", "commit_final"]
    assert [a.rolled_back for a in attempts] == [True, False]
    assert [a.committed for a in attempts] == [False, True]
    assert runtime.value == 2
    assert runtime.seen is not None
    assert [t.price for t in runtime.seen] == [11.0]
    assert strategy.seen_closes == [11.0]
    assert strategy.flag == "after"


def test_guarded_strategy_tick_invocation_rejects_final_tick_order_commands() -> None:
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    runtime = RuntimeWithRealtimeTicks()
    strategy = StrategyAddsBufferedOrder(ctx)

    with pytest.raises(
        ConfigError,
        match="order commands require TradingView intrabar order/fill oracle",
    ):
        engine._guarded_realtime_strategy_tick_loop_skeleton(
            _slice(),
            ctx=ctx,
            strategy=strategy,
            runtime=runtime,
            commit_policy=RealtimeTickCommitPolicy(commit_final_tick=True),
        )

    assert ctx.buffer.commands == []
    assert runtime.value == 1
    assert runtime.seen == []
    assert strategy.seen_closes == []


def test_guarded_strategy_tick_invocation_rejects_intrabar_fill_commit_policy() -> None:
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    strategy = StrategyRecordsTickBars()

    with pytest.raises(ConfigError, match="tick oracle"):
        engine._guarded_realtime_strategy_tick_loop_skeleton(
            _slice(),
            ctx=ctx,
            strategy=strategy,
            commit_policy=RealtimeTickCommitPolicy(allow_intrabar_order_fills=True),
        )
