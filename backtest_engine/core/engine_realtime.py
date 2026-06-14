"""Realtime checkpoint helpers for :class:`BacktestEngine`."""

from __future__ import annotations

from inspect import signature
from typing import Any

from backtest_engine.context import StrategyContext, StrategyStateView
from backtest_engine.core.deterministic_hash import sha256_obj
from backtest_engine.core.realtime import (
    BarTickSlice,
    RealtimeTickAttempt,
    RealtimeTickCommitPolicy,
)
from backtest_engine.core.realtime_tick_loop import (
    guarded_realtime_strategy_tick_loop_skeleton,
    guarded_realtime_tick_loop_skeleton,
)
from backtest_engine.core.state_snapshot import (
    RealtimeBrokerSnapshot,
    RealtimeExecutionCheckpoint,
    clone_state,
)
from backtest_engine.errors import ResumeUnsupportedError


class EngineRealtimeMixin:
    def _config_hash(self) -> str:
        snapshot = self.config.snapshot()
        snapshot.pop("export_resume_state", None)
        return sha256_obj(snapshot)

    def _export_realtime_broker_state(self) -> RealtimeBrokerSnapshot:
        """Export a detached broker checkpoint for future realtime tick rollback."""

        return RealtimeBrokerSnapshot(
            cash=self.cash,
            equity=self.equity,
            peak_equity=self.peak_equity,
            max_drawdown=self.max_drawdown,
            max_drawdown_percent=self.max_drawdown_percent,
            trough_equity=self.trough_equity,
            max_runup=self.max_runup,
            max_runup_percent=self.max_runup_percent,
            position=clone_state(self.position),
            orders=clone_state(self.orders),
            fills=clone_state(self.fills),
            closed_trades=clone_state(self.closed_trades),
            open_trades=clone_state(self.open_trades),
            last_trade_bar=self.last_trade_bar,
            events=clone_state(self.events),
            warnings=clone_state(self.warnings),
            errors=clone_state(self.errors),
        )

    def _restore_realtime_broker_state(
        self, snapshot: RealtimeBrokerSnapshot, ctx: StrategyContext | None = None
    ) -> None:
        """Restore a detached broker checkpoint and refresh StrategyStateView refs."""

        if not isinstance(snapshot, RealtimeBrokerSnapshot):
            raise ResumeUnsupportedError(
                "realtime broker rollback requires a RealtimeBrokerSnapshot"
            )
        self.cash = snapshot.cash
        self.equity = snapshot.equity
        self.peak_equity = snapshot.peak_equity
        self.max_drawdown = snapshot.max_drawdown
        self.max_drawdown_percent = snapshot.max_drawdown_percent
        self.trough_equity = snapshot.trough_equity
        self.max_runup = snapshot.max_runup
        self.max_runup_percent = snapshot.max_runup_percent
        self.position = clone_state(snapshot.position)
        self.orders = clone_state(snapshot.orders)
        self.fills = clone_state(snapshot.fills)
        self.closed_trades = clone_state(snapshot.closed_trades)
        self.open_trades = clone_state(snapshot.open_trades)
        self._filled_exit_entry_keys = {
            (
                trade.exit_id.split(":", 1)[0],
                trade.entry_id,
                trade.entry_time,
                trade.entry_bar_index,
            )
            for trade in self.closed_trades
            if trade.exit_id is not None
        }
        self.last_trade_bar = snapshot.last_trade_bar
        self.events = clone_state(snapshot.events)
        self.warnings = clone_state(snapshot.warnings)
        self.errors = clone_state(snapshot.errors)
        self.state = StrategyStateView(
            initial_capital=self.config.initial_capital,
            cash=self.cash,
            equity=self.equity,
            _open_trades_ref=self.open_trades,
            _closed_trades_ref=self.closed_trades,
        )
        if ctx is not None:
            ctx.state = self.state
        self._update_state()

    def _export_realtime_execution_checkpoint(
        self, *, strategy: Any | None = None, runtime: Any | None = None
    ) -> RealtimeExecutionCheckpoint:
        """Export combined broker/runtime/strategy checkpoint for tick rollback."""

        runtime_export = (
            getattr(runtime, "export_state", None) if runtime is not None else None
        )
        strategy_export = (
            getattr(strategy, "export_state", None) if strategy is not None else None
        )
        runtime_state = None
        if callable(runtime_export):
            try:
                params = signature(runtime_export).parameters
                runtime_state = (
                    runtime_export(include_varip=False)
                    if "include_varip" in params
                    else runtime_export()
                )
            except (TypeError, ValueError):
                runtime_state = runtime_export()
        return RealtimeExecutionCheckpoint(
            broker_state=self._export_realtime_broker_state(),
            runtime_state=clone_state(runtime_state),
            strategy_state=(
                clone_state(strategy_export()) if callable(strategy_export) else None
            ),
        )

    def _restore_realtime_execution_checkpoint(
        self,
        checkpoint: RealtimeExecutionCheckpoint,
        *,
        ctx: StrategyContext | None = None,
        strategy: Any | None = None,
        runtime: Any | None = None,
    ) -> None:
        """Restore combined broker/runtime/strategy checkpoint for tick rollback."""

        if not isinstance(checkpoint, RealtimeExecutionCheckpoint):
            raise ResumeUnsupportedError(
                "realtime execution rollback requires a RealtimeExecutionCheckpoint"
            )
        self._restore_realtime_broker_state(checkpoint.broker_state, ctx)
        if checkpoint.runtime_state is not None:
            restore = (
                getattr(runtime, "restore_state", None) if runtime is not None else None
            )
            if not callable(restore):
                raise ResumeUnsupportedError(
                    "runtime_state is present but runtime does not implement restore_state(state)"
                )
            restore(clone_state(checkpoint.runtime_state))
        if checkpoint.strategy_state is not None:
            restore = (
                getattr(strategy, "restore_state", None)
                if strategy is not None
                else None
            )
            if not callable(restore):
                raise ResumeUnsupportedError(
                    "strategy_state is present but strategy does not implement restore_state(state)"
                )
            restore(clone_state(checkpoint.strategy_state))

    def _guarded_realtime_tick_loop_skeleton(
        self,
        tick_slice: BarTickSlice,
        *,
        ctx: StrategyContext,
        strategy: Any | None = None,
        runtime: Any | None = None,
        on_attempt: Any | None = None,
    ) -> tuple[RealtimeTickAttempt, ...]:
        """Create rollback-guarded tick attempts without enabling tick execution.

        Each tick gets a combined execution checkpoint, optional local mutation
        hook, and immediate restore. The method is intentionally not called from
        ``run()`` while ``calc_on_every_tick`` remains fail-closed.
        """

        return guarded_realtime_tick_loop_skeleton(
            self,
            tick_slice,
            ctx=ctx,
            strategy=strategy,
            runtime=runtime,
            on_attempt=on_attempt,
        )

    def _guarded_realtime_strategy_tick_loop_skeleton(
        self,
        tick_slice: BarTickSlice,
        *,
        ctx: StrategyContext,
        strategy: Any,
        runtime: Any | None = None,
        commit_policy: RealtimeTickCommitPolicy | None = None,
    ) -> tuple[RealtimeTickAttempt, ...]:
        """Invoke strategy once per tick under rollback, without committing effects.

        This is a guarded scaffold for future ``calc_on_every_tick`` work. It is
        intentionally not wired into ``run()`` and restores broker, runtime,
        strategy, and command-buffer state after every attempted tick.
        """

        return guarded_realtime_strategy_tick_loop_skeleton(
            self,
            tick_slice,
            ctx=ctx,
            strategy=strategy,
            runtime=runtime,
            commit_policy=commit_policy,
        )
