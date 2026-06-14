"""Guarded realtime tick loop scaffolds."""

from __future__ import annotations

from typing import Any

from backtest_engine.context import StrategyContext
from backtest_engine.core.realtime import (
    BarTickSlice,
    RealtimeTickAttempt,
    RealtimeTickCommitPolicy,
    RuntimeTickUpdate,
    validate_realtime_order_fill_oracle_proof,
)
from backtest_engine.core.state_snapshot import clone_state
from backtest_engine.errors import ConfigError


def guarded_realtime_tick_loop_skeleton(
    engine: Any,
    tick_slice: BarTickSlice,
    *,
    ctx: StrategyContext,
    strategy: Any | None = None,
    runtime: Any | None = None,
    on_attempt: Any | None = None,
) -> tuple[RealtimeTickAttempt, ...]:
    attempts: list[RealtimeTickAttempt] = []
    for tick_index, tick in enumerate(tick_slice.ticks):
        checkpoint = engine._export_realtime_execution_checkpoint(
            strategy=strategy, runtime=runtime
        )
        if callable(on_attempt):
            on_attempt(tick, tick_index)
        engine._restore_realtime_execution_checkpoint(
            checkpoint, ctx=ctx, strategy=strategy, runtime=runtime
        )
        attempts.append(
            RealtimeTickAttempt(
                bar_index=tick_slice.bar_index,
                tick_index=tick_index,
                tick=tick,
                checkpoint=checkpoint,
                rolled_back=True,
            )
        )
    return tuple(attempts)


def guarded_realtime_strategy_tick_loop_skeleton(
    engine: Any,
    tick_slice: BarTickSlice,
    *,
    ctx: StrategyContext,
    strategy: Any,
    runtime: Any | None = None,
    commit_policy: RealtimeTickCommitPolicy | None = None,
) -> tuple[RealtimeTickAttempt, ...]:
    policy = commit_policy or RealtimeTickCommitPolicy()
    if policy.allow_intrabar_order_fills:
        validate_realtime_order_fill_oracle_proof(
            policy.intrabar_order_fill_oracle_proof
        )
    attempts: list[RealtimeTickAttempt] = []
    update_realtime_tick = (
        getattr(runtime, "update_realtime_tick", None) if runtime is not None else None
    )
    total_ticks = len(tick_slice.ticks)
    for tick_index, tick in enumerate(tick_slice.ticks):
        action = policy.action_for(tick_index, total_ticks)
        checkpoint = engine._export_realtime_execution_checkpoint(
            strategy=strategy, runtime=runtime
        )
        buffered_commands = clone_state(ctx.buffer.commands)
        committed = False
        try:
            current_bar = tick_slice.bar
            if callable(update_realtime_tick):
                maybe_bar = update_realtime_tick(
                    RuntimeTickUpdate(
                        price=tick.price,
                        volume=float(tick.volume or 0.0),
                        time=tick.time,
                        is_final=tick_index == total_ticks - 1,
                    )
                )
                if maybe_bar is not None:
                    current_bar = maybe_bar
            engine._call_strategy(strategy, current_bar, tick_slice.bar_index)
            if action == "commit_final" and len(ctx.buffer.commands) != len(
                buffered_commands
            ):
                raise ConfigError(
                    "realtime order commands require TradingView intrabar order/fill oracle evidence"
                )
            committed = action == "commit_final"
        finally:
            if not committed:
                engine._restore_realtime_execution_checkpoint(
                    checkpoint, ctx=ctx, strategy=strategy, runtime=runtime
                )
                ctx.buffer.commands = clone_state(buffered_commands) or []
        attempts.append(
            RealtimeTickAttempt(
                bar_index=tick_slice.bar_index,
                tick_index=tick_index,
                tick=tick,
                checkpoint=checkpoint,
                rolled_back=not committed,
                strategy_invoked=True,
                policy=action,
                committed=committed,
            )
        )
    return tuple(attempts)
