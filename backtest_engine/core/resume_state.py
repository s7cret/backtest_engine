"""Resume-state export and restore orchestration for BacktestEngine."""

from __future__ import annotations

import math
from typing import Any, cast

from backtest_engine.context import StrategyContext, StrategyStateView
from backtest_engine.core.deterministic_hash import sha256_obj
from backtest_engine.core.realtime import realtime_tick_schedule_fingerprint
from backtest_engine.core.state_snapshot import (
    BrokerSnapshot,
    build_resume_state,
    clone_state,
)
from backtest_engine.errors import ResumeUnsupportedError
from backtest_engine.models import (
    BacktestResumeState,
    BarSeries,
    Diagnostic,
    EquityPoint,
    Trade,
)

_STRICT_STATISTICS_LISTS = (
    "equity_curve",
    "score_equity_points",
    "events",
    "warnings",
    "errors",
)
_STRICT_STATISTICS_COUNTS = (
    "closed_trade_stats_count",
    "engine_closed_trade_stats_count",
    "engine_win_trades_total",
    "engine_loss_trades_total",
    "engine_even_trades_total",
)
_STRICT_STATISTICS_TOTALS = ("engine_gross_profit_total", "engine_gross_loss_total")


def bar_prefix_fingerprint(series: BarSeries, count: int) -> str:
    bounded_count = max(0, min(count, len(series)))
    return sha256_obj(
        {
            "requested_count": max(0, count),
            "bars": [
                {
                    "time": bar.time,
                    "time_close": bar.time_close,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
                for bar in (series.get_bar(index) for index in range(bounded_count))
            ],
        }
    )


def _validate_strict_statistics_state(
    resume_state: BacktestResumeState, *, label: str
) -> dict[str, Any]:
    statistics = resume_state.statistics_state
    if not isinstance(statistics, dict):
        raise ResumeUnsupportedError(f"{label} state is missing statistics_state")
    required = {
        *_STRICT_STATISTICS_LISTS,
        *_STRICT_STATISTICS_COUNTS,
        *_STRICT_STATISTICS_TOTALS,
    }
    missing = sorted(required - statistics.keys())
    if missing:
        raise ResumeUnsupportedError(
            f"{label} statistics_state is missing fields: {missing}"
        )
    for name in _STRICT_STATISTICS_LISTS:
        if not isinstance(statistics[name], list):
            raise ResumeUnsupportedError(
                f"{label} statistics_state.{name} must be a list"
            )
    for name in _STRICT_STATISTICS_COUNTS:
        value = statistics[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ResumeUnsupportedError(
                f"{label} statistics_state.{name} must be a non-negative integer"
            )
    for name in _STRICT_STATISTICS_TOTALS:
        value = statistics[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ResumeUnsupportedError(
                f"{label} statistics_state.{name} must be finite and non-negative"
            )
    return statistics


def _validate_strict_statistics_against_broker(
    statistics: dict[str, Any], broker: BrokerSnapshot, *, score_start_index: int
) -> None:
    if any(not isinstance(trade, Trade) for trade in broker.closed_trades):
        raise ResumeUnsupportedError(
            "strict resume broker_state.closed_trades must contain Trade values"
        )
    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    losses = 0
    evens = 0
    for trade in broker.closed_trades:
        if trade.profit > 0:
            gross_profit += trade.profit
            wins += 1
        elif trade.profit < 0:
            gross_loss += abs(trade.profit)
            losses += 1
        else:
            evens += 1
    expected = {
        "closed_trade_stats_count": sum(
            1
            for trade in broker.closed_trades
            if trade.exit_bar_index is not None
            and trade.exit_bar_index >= score_start_index
        ),
        "engine_closed_trade_stats_count": len(broker.closed_trades),
        "engine_gross_profit_total": gross_profit,
        "engine_gross_loss_total": gross_loss,
        "engine_win_trades_total": wins,
        "engine_loss_trades_total": losses,
        "engine_even_trades_total": evens,
    }
    for name, value in expected.items():
        if statistics[name] != value:
            raise ResumeUnsupportedError(
                f"strict resume statistics_state.{name} does not match "
                "broker_state.closed_trades"
            )


def prevalidate_strict_resume_before_external_state(
    engine: Any, resume_state: BacktestResumeState
) -> None:
    """Reject context-free strict checkpoint corruption before runtime mutation."""

    if engine.config.resume_validation_policy != "strict":
        return
    statistics = _validate_strict_statistics_state(
        resume_state,
        label="strict resume",
    )
    broker = resume_state.broker_state
    if not isinstance(broker, BrokerSnapshot):
        raise ResumeUnsupportedError(
            "resume_state.broker_state must be a BrokerSnapshot from core.state_snapshot"
        )
    _validate_strict_statistics_against_broker(
        statistics,
        broker,
        score_start_index=max(0, engine._score_start_index),
    )


def _validate_strict_tick_state(resume_state: BacktestResumeState) -> dict[str, Any]:
    if resume_state.strategy_state is None:
        raise ResumeUnsupportedError(
            "strict tick resume state is missing strategy_state"
        )
    if resume_state.runtime_state is None:
        raise ResumeUnsupportedError(
            "strict tick resume state is missing runtime_state"
        )
    return _validate_strict_statistics_state(resume_state, label="strict tick resume")


_NO_ROLLBACK_STATE = object()


def _restore_external_states(
    *,
    strict: bool,
    runtime: Any,
    runtime_restore: Any,
    runtime_state: object | None,
    strategy: Any,
    strategy_restore: Any,
    strategy_state: object | None,
) -> None:
    entries: list[tuple[str, Any, Any, object, tuple[str, ...]]] = []
    if runtime_restore is not None and runtime_state is not None:
        entries.append(
            (
                "runtime_state",
                runtime,
                runtime_restore,
                runtime_state,
                ("export_state",),
            )
        )
    if strategy_restore is not None and strategy_state is not None:
        entries.append(
            (
                "strategy_state",
                strategy,
                strategy_restore,
                strategy_state,
                ("export_realtime_state", "export_state"),
            )
        )

    rollback_states: list[object] = []
    for label, target, _restore, _state, export_names in entries:
        exporter = next(
            (
                candidate
                for name in export_names
                if callable(candidate := getattr(target, name, None))
            ),
            None,
        )
        if strict and exporter is None:
            raise ResumeUnsupportedError(
                f"{label} is present but the target cannot export rollback state"
            )
        try:
            rollback_states.append(
                clone_state(exporter()) if exporter is not None else _NO_ROLLBACK_STATE
            )
        except Exception as exc:
            raise ResumeUnsupportedError(
                f"{label} rollback state export failed before restore"
            ) from exc

    attempted = 0
    try:
        for _label, _target, restore, state, _export_names in entries:
            attempted += 1
            restore(clone_state(state))
    except Exception as exc:
        failed_label = entries[attempted - 1][0]
        rollback_failed = False
        for index in range(attempted - 1, -1, -1):
            rollback_state = rollback_states[index]
            if rollback_state is _NO_ROLLBACK_STATE:
                continue
            try:
                entries[index][2](clone_state(rollback_state))
            except Exception:
                rollback_failed = True
        suffix = " and rollback failed" if rollback_failed else ""
        raise ResumeUnsupportedError(f"{failed_label} restore failed{suffix}") from exc


def restore_resume_state(
    engine: Any,
    resume_state: BacktestResumeState,
    strategy: Any,
    runtime: Any,
    ctx: StrategyContext,
    series: BarSeries | None = None,
) -> int:
    cursor = resume_state.bar_index
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < -1:
        raise ResumeUnsupportedError(
            "resume_state.bar_index must be an integer greater than or equal to -1"
        )
    if series is not None and cursor >= len(series):
        raise ResumeUnsupportedError("resume_state.bar_index must reference an available input bar")
    if resume_state.broker_state is None:
        raise ResumeUnsupportedError(
            "resume_state is missing broker_state; use BacktestEngine export_resume_state or provide a compatible snapshot"
        )
    expected_hash = engine._config_hash()
    if resume_state.config_snapshot_hash != expected_hash:
        msg = "resume state config hash does not match current config snapshot"
        if engine.config.resume_validation_policy == "strict":
            raise ResumeUnsupportedError(msg)
        engine._diag("RESUME_CONFIG_MISMATCH", msg, "warning")
    expected_tick_fingerprint = resume_state.metadata.get("realtime_tick_schedule_fingerprint")
    current_schedule = getattr(engine, "_realtime_tick_schedule", None)
    strict_resume = engine.config.resume_validation_policy == "strict"
    strict_tick_resume = current_schedule is not None and strict_resume
    if strict_tick_resume and expected_tick_fingerprint is None:
        raise ResumeUnsupportedError(
            "strict tick resume state is missing tick schedule fingerprint"
        )
    if expected_tick_fingerprint is not None and current_schedule is not None:
        processed_schedule = current_schedule[: max(0, resume_state.bar_index + 1)]
        current_tick_fingerprint = realtime_tick_schedule_fingerprint(processed_schedule)
        if current_tick_fingerprint != expected_tick_fingerprint:
            msg = "resume state tick schedule fingerprint does not match processed ticks"
            if engine.config.resume_validation_policy == "strict":
                raise ResumeUnsupportedError(msg)
            engine._diag("RESUME_TICK_SCHEDULE_MISMATCH", msg, "warning")
    expected_bar_fingerprint = resume_state.metadata.get("bar_prefix_fingerprint")
    if (
        series is not None
        and expected_bar_fingerprint is None
        and engine.config.resume_validation_policy == "strict"
    ):
        raise ResumeUnsupportedError("strict resume state is missing bar prefix fingerprint")
    if expected_bar_fingerprint is not None and series is not None:
        current_bar_fingerprint = bar_prefix_fingerprint(series, max(0, resume_state.bar_index + 1))
        if current_bar_fingerprint != expected_bar_fingerprint:
            msg = "resume state bar prefix fingerprint does not match processed bars"
            if engine.config.resume_validation_policy == "strict":
                raise ResumeUnsupportedError(msg)
            engine._diag("RESUME_BAR_PREFIX_MISMATCH", msg, "warning")
    statistics_state = (
        _validate_strict_tick_state(resume_state)
        if strict_tick_resume
        else (
            _validate_strict_statistics_state(resume_state, label="strict resume")
            if strict_resume
            else resume_state.statistics_state
        )
    )
    if strict_resume:
        strict_statistics = cast(dict[str, Any], statistics_state)
        collect_equity = engine._want("equity_curve") or engine.config.collect_equity_curve
        if collect_equity:
            equity_curve = strict_statistics["equity_curve"]
            expected_points = max(0, cursor + 1)
            if len(equity_curve) != expected_points:
                raise ResumeUnsupportedError(
                    "strict resume statistics_state.equity_curve must contain exactly "
                    f"{expected_points} processed-bar points"
                )
            if any(
                not isinstance(point, EquityPoint) or point.bar_index != index
                for index, point in enumerate(equity_curve)
            ):
                raise ResumeUnsupportedError(
                    "strict resume statistics_state.equity_curve has invalid bar indices"
                )
        score_equity_points = strict_statistics["score_equity_points"]
        score_start = max(0, engine._score_start_index)
        expected_score_indices = (
            list(range(score_start, cursor + 1))
            if engine._score_mode and collect_equity and cursor >= score_start
            else []
        )
        if len(score_equity_points) != len(expected_score_indices) or any(
            not isinstance(point, EquityPoint) or point.bar_index != expected_index
            for point, expected_index in zip(
                score_equity_points, expected_score_indices, strict=True
            )
        ):
            raise ResumeUnsupportedError(
                "strict resume statistics_state.score_equity_points does not match "
                "the processed score window"
            )
        for name in ("events", "warnings", "errors"):
            if any(not isinstance(item, Diagnostic) for item in strict_statistics[name]):
                raise ResumeUnsupportedError(
                    f"strict resume statistics_state.{name} must contain diagnostics"
                )
    try:
        statistics_state = clone_state(statistics_state)
    except Exception as exc:
        raise ResumeUnsupportedError("resume statistics_state could not be detached") from exc
    broker = resume_state.broker_state
    if not isinstance(broker, BrokerSnapshot):
        raise ResumeUnsupportedError(
            "resume_state.broker_state must be a BrokerSnapshot from core.state_snapshot"
        )
    try:
        broker = clone_state(broker)
    except Exception as exc:
        raise ResumeUnsupportedError("resume broker_state could not be detached") from exc
    if strict_resume:
        _validate_strict_statistics_against_broker(
            cast(dict[str, Any], statistics_state),
            broker,
            score_start_index=max(0, engine._score_start_index),
        )
    from backtest_engine.core.risk_rules import validate_snapshot_risk, restore_risk_state

    validate_snapshot_risk(broker)
    runtime_restore = None
    if resume_state.runtime_state is not None:
        runtime_restore = getattr(runtime, "restore_state", None)
        if not callable(runtime_restore):
            raise ResumeUnsupportedError(
                "runtime_state is present but runtime does not implement restore_state(state)"
            )
    strategy_restore = None
    if resume_state.strategy_state is not None:
        strategy_restore = getattr(strategy, "restore_realtime_state", None) or getattr(
            strategy, "restore_state", None
        )
        if not callable(strategy_restore):
            raise ResumeUnsupportedError(
                "strategy_state is present but strategy does not implement restore_state(state)"
            )
    _restore_external_states(
        strict=strict_resume,
        runtime=runtime,
        runtime_restore=runtime_restore,
        runtime_state=resume_state.runtime_state,
        strategy=strategy,
        strategy_restore=strategy_restore,
        strategy_state=resume_state.strategy_state,
    )
    if broker.risk_state is not None:
        restore_risk_state(engine, broker.risk_state)
    engine.cash = broker.cash
    engine.equity = broker.equity
    engine.peak_equity = broker.peak_equity
    engine.max_drawdown = broker.max_drawdown
    engine.max_drawdown_percent = broker.max_drawdown_percent
    engine.trough_equity = broker.trough_equity
    engine.max_runup = broker.max_runup
    engine.max_runup_percent = broker.max_runup_percent
    engine.position = broker.position
    engine._all_entry_exits = broker.all_entry_exits
    engine.orders = broker.orders
    engine.fills = broker.fills
    engine.closed_trades = broker.closed_trades
    engine.open_trades = broker.open_trades
    engine._filled_exit_entry_keys = {
        (
            trade.exit_parent_id or trade.exit_id.split(":", 1)[0],
            trade.entry_id,
            trade.entry_time,
            trade.entry_bar_index,
            trade.entry_fill_index,
        )
        for trade in engine.closed_trades
        if trade.exit_id is not None
    }
    engine.last_trade_bar = broker.last_trade_bar
    engine.state = StrategyStateView(
        initial_capital=engine.config.initial_capital,
        cash=engine.cash,
        equity=engine.equity,
        _open_trades_ref=engine.open_trades,
        _closed_trades_ref=engine.closed_trades,
    )
    ctx.state = engine.state
    engine._update_state()
    if isinstance(statistics_state, dict):
        engine._resume_equity_curve_history = list(statistics_state.get("equity_curve", []))
        engine._score_equity_points = list(statistics_state.get("score_equity_points", []))
        current_warnings = list(engine.warnings)
        current_errors = list(engine.errors)
        engine.events = list(statistics_state.get("events", [])) + list(engine.events)
        engine.warnings = list(statistics_state.get("warnings", [])) + current_warnings
        engine.errors = list(statistics_state.get("errors", [])) + current_errors
        engine_statistics = {
            "closed_trade_stats_count": "_closed_trade_stats_count",
            "gross_profit_total": "_gross_profit_total",
            "gross_loss_total": "_gross_loss_total",
            "win_trades_total": "_win_trades_total",
            "loss_trades_total": "_loss_trades_total",
            "even_trades_total": "_even_trades_total",
        }
        for name, attribute in engine_statistics.items():
            key = f"engine_{name}"
            if key in statistics_state:
                setattr(engine, attribute, statistics_state[key])
    return max(0, resume_state.bar_index + 1)


def export_resume_state(
    engine: Any,
    bar_index: int,
    strategy: Any | None = None,
    runtime: Any | None = None,
    series: BarSeries | None = None,
) -> BacktestResumeState:
    strategy_export = (
        getattr(strategy, "export_realtime_state", None) or getattr(strategy, "export_state", None)
        if strategy is not None
        else None
    )
    runtime_export = getattr(runtime, "export_state", None) if runtime is not None else None
    strategy_state = strategy_export() if callable(strategy_export) else None
    runtime_state = runtime_export() if callable(runtime_export) else None
    if strategy is not None and strategy_state is None:
        engine._diag(
            "RESUME_STRATEGY_STATE_UNAVAILABLE",
            "strategy does not implement export_state(); resume snapshot contains engine/runtime state only",
            "warning",
        )
    from backtest_engine.core.risk_rules import capture_risk_state

    broker = BrokerSnapshot(
        engine.cash,
        engine.equity,
        engine.peak_equity,
        engine.max_drawdown,
        engine.max_drawdown_percent,
        engine.trough_equity,
        engine.max_runup,
        engine.max_runup_percent,
        engine.position,
        engine.orders,
        engine.fills,
        engine.closed_trades,
        engine.open_trades,
        engine.last_trade_bar,
        engine._all_entry_exits,
        capture_risk_state(engine),
        1,
    )
    metadata: dict[str, Any] = {"resume_contract": "engine-broker-snapshot-v1"}
    if series is not None:
        metadata["bar_prefix_fingerprint"] = bar_prefix_fingerprint(series, max(0, bar_index + 1))
    tick_schedule = getattr(engine, "_realtime_tick_schedule", None)
    if tick_schedule is not None:
        metadata["realtime_tick_schedule_fingerprint"] = realtime_tick_schedule_fingerprint(
            tick_schedule[: max(0, bar_index + 1)]
        )
    return build_resume_state(
        bar_index=bar_index,
        config_snapshot_hash=engine._config_hash(),
        broker_state=broker,
        strategy_state=strategy_state,
        runtime_state=runtime_state,
        statistics_state={
            "equity_curve": clone_state(getattr(engine, "_resume_equity_curve_history", [])),
            "score_equity_points": clone_state(engine._score_equity_points),
            "events": clone_state(engine.events),
            "warnings": clone_state(engine.warnings),
            "errors": clone_state(engine.errors),
            "closed_trade_stats_count": sum(
                1
                for trade in engine.closed_trades
                if trade.exit_bar_index is not None
                and trade.exit_bar_index >= engine._score_start_index
            ),
            "engine_closed_trade_stats_count": engine._closed_trade_stats_count,
            "engine_gross_profit_total": engine._gross_profit_total,
            "engine_gross_loss_total": engine._gross_loss_total,
            "engine_win_trades_total": engine._win_trades_total,
            "engine_loss_trades_total": engine._loss_trades_total,
            "engine_even_trades_total": engine._even_trades_total,
        },
        metadata=metadata,
    )
