"""Deterministic explicit-tick execution for ``calc_on_every_tick``."""

from __future__ import annotations

import inspect
import time
from typing import Any, Literal

from backtest_engine.context import StrategyContext
from backtest_engine.core.realtime import (
    realtime_tick_schedule_fingerprint,
    resolve_realtime_tick_schedule,
)
from backtest_engine.core.resume_state import (
    prevalidate_strict_resume_before_external_state,
)
from backtest_engine.core.state_snapshot import clone_state
from backtest_engine.errors import TickReplayStateError
from backtest_engine.models import (
    BacktestResumeState,
    Bar,
    BarSeries,
    EquityPoint,
    Tick,
)
from backtest_engine.results import BacktestResult

BacktestStatus = Literal["completed", "failed", "early_stopped"]


def run_realtime_strategy(
    engine: Any,
    strategy_class: type,
    params: dict[str, Any],
    series: BarSeries,
    t0: float,
    resume_state: BacktestResumeState | None,
) -> BacktestResult:
    """Replay complete explicit tick streams, committing each parent bar once."""

    schedule = resolve_realtime_tick_schedule(engine.config, series)
    engine._realtime_tick_schedule = schedule
    engine._realtime_tick_schedule_fingerprint = realtime_tick_schedule_fingerprint(
        schedule
    )
    if resume_state is not None:
        prevalidate_strict_resume_before_external_state(engine, resume_state)
    ctx = StrategyContext(engine.config, engine.state)
    outer_runtime = engine.config.runtime
    _prepare_runtime_for_run(engine, outer_runtime)
    try:
        strategy = strategy_class(params=params, runtime=outer_runtime, ctx=ctx)
    except TypeError:
        strategy = strategy_class(params, outer_runtime)
        strategy.ctx = ctx
    script_runtime = getattr(strategy, "_pine_runtime", outer_runtime)
    _require_rollback_state(strategy, script_runtime)
    if script_runtime is not outer_runtime:
        _prepare_runtime_for_run(engine, script_runtime)

    start_index = 0
    if resume_state is not None:
        start_index = engine._restore_resume_state(
            resume_state, strategy, script_runtime, ctx, series
        )

    equity_curve: list[EquityPoint] | None = (
        (
            list(getattr(engine, "_resume_equity_curve_history", []))
            if resume_state is not None
            else []
        )
        if engine._want("equity_curve") or engine.config.collect_equity_curve
        else None
    )
    status: BacktestStatus = "completed"
    early_reason: str | None = None
    engine._realtime_tick_execution = True
    try:
        for tick_slice in schedule[start_index:]:
            i = tick_slice.bar_index
            parent = tick_slice.bar
            engine._cb("on_bar_start", parent, i)
            _activate_orders(engine, parent, i)
            initial = Bar(
                time=parent.time,
                open=parent.open,
                high=parent.open,
                low=parent.open,
                close=parent.open,
                volume=0.0,
                time_close=parent.time_close,
            )
            _begin_realtime_bar(script_runtime, initial, i)
            engine._begin_realtime_script_bar(strategy, script_runtime)

            for tick_index, tick in enumerate(tick_slice.ticks):
                tick_phase: Literal["non_final", "final"] = (
                    "final" if tick_index == len(tick_slice.ticks) - 1 else "non_final"
                )
                engine._set_realtime_tick_prefix(
                    parent,
                    tick_slice.ticks[: tick_index + 1],
                    tick_index == len(tick_slice.ticks) - 1,
                )
                current = engine._prepare_realtime_strategy_invocation(strategy)
                fill_bar = _tick_fill_bar(parent, tick)
                engine._process_bar_fills(
                    strategy, ctx, fill_bar, i, tick_phase=tick_phase
                )

                # A fill recalculation has its own rollback.  The ordinary tick
                # execution starts from the same committed prior-bar state.
                current = engine._prepare_realtime_strategy_invocation(strategy)
                engine._update_open_profit(tick.price)
                engine._update_state()
                engine._call_strategy(strategy, current, i)
                # Tick-created commands become active now, after this tick's fill
                # scan, and are therefore first eligible on the next explicit tick.
                engine._flush(ctx, current, i, recalc_after_fill=True)

                if (
                    tick_index == len(tick_slice.ticks) - 1
                    and engine.config.process_orders_on_close
                ):
                    engine._process_bar_fills(
                        strategy, ctx, fill_bar, i, tick_phase=tick_phase
                    )

            engine._update_intrabar_drawdown(parent)
            engine._update_open_profit(parent.close)
            engine._update_trade_excursions(parent)
            engine._update_state()
            extremes = engine._update_equity_extremes(engine.equity)
            engine._update_state()
            if equity_curve is not None:
                point = EquityPoint(
                    i,
                    parent.time,
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
                )
                equity_curve.append(point)
                if engine._score_mode and i >= engine._score_start_index:
                    engine._score_equity_points.append(point)
                engine._cb("on_equity", point)
            stop_now, status, early_reason = _early_stop_state(engine, i, extremes)
            script_runtime.end_bar()
            engine._cb("on_bar_end", parent, i, engine.state)
            engine._end_realtime_script_bar()
            if stop_now:
                break
    finally:
        engine._realtime_tick_execution = False
        engine._end_realtime_script_bar()

    finalize = getattr(strategy, "_finalize", None)
    if callable(finalize):
        finalize()
    if (
        engine.config.force_close_on_end
        and engine.position.direction != "flat"
        and len(series)
    ):
        engine._force_close(series.get_bar(len(series) - 1), len(series) - 1)
    engine._resume_equity_curve_history = clone_state(equity_curve or [])
    return engine._result(
        series,
        equity_curve,
        status,
        early_reason,
        (time.perf_counter() - t0) * 1000,
        strategy,
        script_runtime,
    )


def _require_rollback_state(strategy: Any, runtime: Any) -> None:
    strategy_export = getattr(strategy, "export_realtime_state", None) or getattr(
        strategy, "export_state", None
    )
    strategy_restore = getattr(strategy, "restore_realtime_state", None) or getattr(
        strategy, "restore_state", None
    )
    if not callable(strategy_export) or not callable(strategy_restore):
        raise TickReplayStateError(
            "calc_on_every_tick strategy must implement export_state() and restore_state(state)"
        )
    _require_runtime_rollback_state(runtime)


def _require_runtime_rollback_state(runtime: Any) -> tuple[Any, Any]:
    export = getattr(runtime, "export_state", None)
    restore = getattr(runtime, "restore_state", None)
    if not callable(export) or not callable(restore):
        raise TickReplayStateError(
            "calc_on_every_tick runtime must implement export_state() and restore_state(state)"
        )
    try:
        params = inspect.signature(export).parameters
    except (TypeError, ValueError) as exc:
        raise TickReplayStateError(
            "runtime export_state must expose include_varip=False for deterministic rollback"
        ) from exc
    if "include_varip" not in params:
        raise TickReplayStateError(
            "runtime export_state must expose include_varip=False for deterministic rollback"
        )
    return export, restore


def _prepare_runtime_for_run(engine: Any, runtime: Any) -> None:
    """Restore a caller-supplied runtime to its detached first-run baseline."""

    export, restore = _require_runtime_rollback_state(runtime)
    baselines = getattr(engine, "_fresh_runtime_baselines", None)
    if baselines is None:
        baselines = []
        engine._fresh_runtime_baselines = baselines
    for target, baseline in baselines:
        if target is runtime:
            try:
                restore(clone_state(baseline))
            except Exception as exc:
                raise TickReplayStateError(
                    "calc_on_every_tick runtime could not restore its fresh-run baseline"
                ) from exc
            return
    try:
        baseline = clone_state(export(include_varip=True))
    except Exception as exc:
        raise TickReplayStateError(
            "calc_on_every_tick runtime could not export its fresh-run baseline"
        ) from exc
    baselines.append((runtime, baseline))


def _begin_realtime_bar(runtime: Any, bar: Bar, bar_index: int) -> None:
    begin = getattr(runtime, "begin_realtime_bar", None)
    if callable(begin):
        begin(bar)
        return
    begin = getattr(runtime, "begin_bar", None)
    if not callable(begin):
        raise TickReplayStateError(
            "calc_on_every_tick runtime must implement begin_realtime_bar(bar)"
        )
    params = inspect.signature(begin).parameters
    begin(bar, bar_index) if len(params) >= 2 else begin(bar)


def _activate_orders(engine: Any, bar: Bar, bar_index: int) -> None:
    for order in engine.orders:
        if order.status == "pending" and order.active_from_bar_index <= bar_index:
            order.status = "active"
            engine._event(
                "ORDER_ACTIVATED",
                f"order {order.id} activated",
                bar_index,
                bar.time,
                order.id,
            )
            engine._cb("on_order_activated", order)


def _tick_fill_bar(parent: Bar, tick: Tick) -> Bar:
    return Bar(
        time=tick.time,
        open=tick.price,
        high=tick.price,
        low=tick.price,
        close=tick.price,
        volume=tick.volume,
        time_close=tick.time,
    )


def _early_stop_state(
    engine: Any, bar_index: int, extremes: Any
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
