"""Native strategy bar-loop orchestration for BacktestEngine."""

from __future__ import annotations

import time
from typing import Any, Literal, Protocol, cast

from backtest_engine.config import BacktestConfig
from backtest_engine.context import StrategyContext
from backtest_engine.context import StrategyStateView
from backtest_engine.models import (
    BacktestResumeState,
    Bar,
    BarSeries,
    EquityPoint,
    Order,
    Position,
)
from backtest_engine.core.state_snapshot import clone_state
from backtest_engine.core.protocol_boundary import emit_protocol_bar_commit
from backtest_engine.results import BacktestResult, EquityExtremes

BacktestStatus = Literal["completed", "failed", "early_stopped"]


class NativeRuntime(Protocol):
    def begin_bar(self, bar: Bar, bar_index: int) -> None: ...

    def end_bar(self) -> None: ...


class NativeRunEngine(Protocol):
    config: BacktestConfig
    state: StrategyStateView
    orders: list[Order]
    position: Position
    cash: float
    equity: float
    _score_mode: bool
    _score_start_index: int
    _score_equity_points: list[EquityPoint]
    _early_stop_enabled: bool
    _min_equity_stop: float | None
    _max_drawdown_stop_percent: float | None
    _max_drawdown_stop_cash: float | None
    _max_bars_without_trade: int | None
    last_trade_bar: int | None
    last_strategy: Any
    _prehistory_end_index: int
    _warmup_machine: Any
    warmup_phase: Any
    score_opening_broker: Any
    warmup_boundary_buffer_len_before: int | None
    warmup_boundary_buffer_len_after: int | None
    _current_phase: str | None
    _last_processed_bar_index: int

    def _want(self, name: str) -> bool: ...
    def _restore_resume_state(
        self,
        resume_state: BacktestResumeState,
        strategy: Any,
        runtime: Any,
        ctx: StrategyContext,
        series: BarSeries,
    ) -> int: ...
    def _cb(self, name: str, *args: Any) -> None: ...
    def _event(
        self,
        code: str,
        message: str,
        bar_index: int | None = None,
        time: int | None = None,
        order_id: str | None = None,
    ) -> None: ...
    def _process_bar_fills(
        self,
        strategy: Any,
        ctx: StrategyContext,
        bar: Bar,
        i: int,
        *,
        open_only: bool = False,
        skip_open: bool = False,
        close_activation_only: bool = False,
        skip_trailing: bool = False,
        trailing_only: bool = False,
        tick_phase: Literal["non_final", "final"] | None = None,
    ) -> None: ...
    def _update_open_profit(self, price: float) -> None: ...
    def _update_state(self) -> None: ...
    def _call_strategy(self, strategy: Any, bar: Bar, i: int) -> None: ...

    def _flush(
        self,
        ctx: StrategyContext,
        bar: Bar,
        i: int,
        *,
        recalc_after_fill: bool = False,
    ) -> None: ...
    def _update_intrabar_drawdown(self, bar: Bar) -> None: ...
    def _update_trade_excursions(self, bar: Bar) -> None: ...
    def _update_equity_extremes(self, equity: float) -> EquityExtremes: ...
    def _force_close(self, bar: Bar, bar_index: int) -> None: ...
    def _result(
        self,
        series: BarSeries,
        equity_curve: list[EquityPoint] | None,
        status: BacktestStatus,
        early_reason: str | None,
        duration_ms: float,
        strategy: Any | None = None,
        runtime: Any | None = None,
    ) -> BacktestResult: ...


class NoopRuntime:
    def begin_bar(self, bar: Bar, bar_index: int) -> None:
        pass

    def end_bar(self) -> None:
        pass


def run_native_strategy(
    engine: NativeRunEngine,
    strategy_class: type,
    params: dict[str, Any],
    series: BarSeries,
    t0: float,
    resume_state: BacktestResumeState | None,
) -> BacktestResult:
    if engine.config.calc_on_every_tick:
        from backtest_engine.core.realtime_run_loop import run_realtime_strategy

        return run_realtime_strategy(
            engine, strategy_class, params, series, t0, resume_state
        )
    ctx = StrategyContext(engine.config, engine.state)
    runtime = cast(NativeRuntime, engine.config.runtime or NoopRuntime())
    try:
        strategy = strategy_class(params=params, runtime=runtime, ctx=ctx)
    except TypeError:
        strategy = strategy_class(params, runtime)
        strategy.ctx = ctx
    engine.last_strategy = strategy

    start_index = 0
    if resume_state is not None:
        start_index = engine._restore_resume_state(
            resume_state, strategy, runtime, ctx, series
        )

    from backtest_engine.core.warmup import (
        BrokerState,
        WarmupPhase,
        WarmupPhaseMachine,
        apply_canonical_broker_reset,
        discard_command_buffer,
        stamp_phase,
    )

    machine = None
    if engine.config.warmup_policy:
        score_end = len(series) - 1
        if engine.config.score_end_time is not None:
            for idx in range(len(series)):
                if int(series.time[idx]) <= int(engine.config.score_end_time):
                    score_end = idx
        machine = WarmupPhaseMachine(
            engine.config.warmup_policy,
            warmup_end_index=engine._prehistory_end_index,
            score_end_index=score_end,
            series_len=len(series),
        )
        engine._warmup_machine = machine
        engine.warmup_phase = machine.phase

    collect_equity_curve = (
        engine._want("equity_curve") or engine.config.collect_equity_curve
    )
    equity_curve: list[EquityPoint] | None = None
    if collect_equity_curve:
        equity_curve = (
            list(getattr(engine, "_resume_equity_curve_history", []))
            if resume_state is not None
            else []
        )
    status: BacktestStatus = "completed"
    early_reason: str | None = None
    last_processed_index = start_index - 1
    for i in range(start_index, len(series)):
        bar = series.get_bar(i)
        decision = None
        if machine is not None:
            upcoming = machine._phase_for(i)
            if upcoming is WarmupPhase.SCORE and engine.score_opening_broker is None:
                engine.warmup_boundary_buffer_len_before = len(ctx.buffer.commands)
                if engine.config.warmup_policy != "TRADE_THROUGH_UNSCORED":
                    discard_command_buffer(engine, ctx, i, bar.time)
                if engine.config.warmup_policy == "CALC_THEN_RESET_BROKER":
                    apply_canonical_broker_reset(engine)
                engine.warmup_boundary_buffer_len_after = len(ctx.buffer.commands)
                engine.score_opening_broker = BrokerState.capture(engine)
            decision = machine.begin_bar(i)
            engine.warmup_phase = machine.phase
            engine._current_phase = decision.phase.label
        engine._cb("on_bar_start", bar, i)
        for order in engine.orders:
            if order.status == "pending" and order.active_from_bar_index <= i:
                order.status = "active"
                engine._event(
                    "ORDER_ACTIVATED",
                    f"order {order.id} activated",
                    i,
                    bar.time,
                    order.id,
                )
                engine._cb("on_order_activated", order)
        runtime.begin_bar(bar, i)
        allow_broker = decision is None or decision.broker_execution_allowed
        if allow_broker:
            engine._process_bar_fills(strategy, ctx, bar, i, open_only=True)
            engine._process_bar_fills(strategy, ctx, bar, i, skip_open=True)
        engine._update_open_profit(bar.close)
        engine._update_state()
        engine._call_strategy(strategy, bar, i)
        if allow_broker:
            engine._flush(ctx, bar, i)
            if (
                engine.config.process_orders_on_close
                or engine.config.calc_on_order_fills
            ):
                engine._process_bar_fills(strategy, ctx, bar, i, skip_open=True)
            else:
                engine._process_bar_fills(
                    strategy,
                    ctx,
                    bar,
                    i,
                    skip_open=True,
                    close_activation_only=True,
                )
        elif decision is not None and not decision.broker_execution_allowed:
            discard_command_buffer(engine, ctx, i, bar.time)
        stamp_phase(engine, getattr(engine, "_current_phase", None))
        engine._update_intrabar_drawdown(bar)
        engine._update_open_profit(bar.close)
        engine._update_trade_excursions(bar)
        engine._update_state()
        extremes = engine._update_equity_extremes(engine.equity)
        engine._update_state()
        if equity_curve is not None:
            point = EquityPoint(
                i,
                bar.time,
                engine.equity,
                engine.cash,
                engine.position.size,
                (
                    engine.position.avg_price
                    if engine.position.direction != "flat"
                    else None
                ),
                engine.position.open_profit,
                engine.position.realized_profit,
                extremes.drawdown,
                extremes.drawdown_percent,
                extremes.runup,
                extremes.runup_percent,
                phase=getattr(engine, "_current_phase", None),
            )
            equity_curve.append(point)
            if engine._score_mode and i >= engine._score_start_index:
                engine._score_equity_points.append(point)
            engine._cb("on_equity", point)
        stop_now, status, early_reason = _early_stop_state(engine, i, extremes)
        runtime.end_bar()
        emit_protocol_bar_commit(engine, strategy, bar, i)
        engine._cb("on_bar_end", bar, i, engine.state)
        last_processed_index = i
        if stop_now:
            break
        if machine is not None:
            machine.end_bar(i)
            if (
                machine.phase is WarmupPhase.SCORE
                and i == machine.score_end_index
                and engine.config.score_end_policy == "FORCE_CLOSE"
                and engine.position.direction != "flat"
            ):
                engine._force_close(bar, i)
                stamp_phase(engine, engine._current_phase)

    finalize = getattr(strategy, "_finalize", None)
    if callable(finalize):
        finalize()
    if (
        engine.config.force_close_on_end
        and engine.position.direction != "flat"
        and len(series)
    ):
        engine._force_close(series.get_bar(len(series) - 1), len(series) - 1)
    setattr(engine, "_resume_equity_curve_history", clone_state(equity_curve or []))
    engine._last_processed_bar_index = last_processed_index
    if machine is not None:
        machine.finalize()
        engine.warmup_phase = machine.phase
        if engine.score_opening_broker is None:
            engine.score_opening_broker = BrokerState.canonical_initial(
                engine.config.initial_capital
            )
            engine.warmup_boundary_buffer_len_before = 0
            engine.warmup_boundary_buffer_len_after = 0
    return engine._result(
        series,
        equity_curve,
        status,
        early_reason,
        (time.perf_counter() - t0) * 1000,
        strategy,
        runtime,
    )


def _early_stop_state(
    engine: NativeRunEngine, bar_index: int, extremes: EquityExtremes
) -> tuple[bool, BacktestStatus, str | None]:
    if not engine._early_stop_enabled:
        return False, "completed", None
    if engine._min_equity_stop is not None and engine.equity <= engine._min_equity_stop:
        return True, "early_stopped", "min_equity_stop"
    if (
        engine._max_drawdown_stop_percent is not None
        and extremes.drawdown_percent >= engine._max_drawdown_stop_percent
    ):
        return True, "early_stopped", "max_drawdown_stop_percent"
    if (
        engine._max_drawdown_stop_cash is not None
        and extremes.drawdown >= engine._max_drawdown_stop_cash
    ):
        return True, "early_stopped", "max_drawdown_stop_cash"
    if (
        engine._max_bars_without_trade is not None
        and engine.last_trade_bar is not None
        and bar_index - engine.last_trade_bar >= engine._max_bars_without_trade
    ):
        return True, "early_stopped", "max_bars_without_trade"
    return False, "completed", None
