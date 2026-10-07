"""Pure owner preflight for typed committed explicit-tick resume.

State schemas belong to the trusted strategy/runtime classes supplied by the
caller. Their class/static validators must completely validate the detached state
without constructing or mutating an owner. JSON cannot name an owner or import it.
"""

from __future__ import annotations

from copy import copy
import inspect
from typing import Any

from backtest_engine.config import BacktestConfig
from backtest_engine.core.realtime import (
    BarTickSlice,
    realtime_tick_schedule_fingerprint,
    resolve_realtime_tick_schedule,
)
from backtest_engine.core.realtime_run_loop import _require_rollback_state
from backtest_engine.core.state_snapshot import clone_state
from backtest_engine.errors import ResumeUnsupportedError
from backtest_engine.models import BacktestResumeState, BarSeries


def admit_explicit_tick_schedule(
    config: BacktestConfig, series: BarSeries
) -> tuple[BarTickSlice, ...]:
    """Freeze and validate all explicit input before consulting a live owner."""
    if config.realtime_tick_provider is not None or type(config.realtime_ticks) not in (
        list,
        tuple,
    ):
        raise ResumeUnsupportedError("typed tick resume requires explicit list/tuple Tick input")
    schedule_config = copy(config)
    schedule_config.realtime_ticks = tuple(config.realtime_ticks)  # type: ignore[arg-type]
    try:
        return resolve_realtime_tick_schedule(schedule_config, series)
    except Exception as error:
        raise ResumeUnsupportedError(
            "typed tick schedule admission failed: " + str(error)
        ) from error


def admit_realtime_resume(
    state: BacktestResumeState | None,
    config: BacktestConfig,
    strategy_class: type,
    series: BarSeries,
    *,
    admitted_schedule: tuple[BarTickSlice, ...] | None = None,
) -> tuple[BarTickSlice, ...]:
    """Admit complete owner graphs before scheduling or any live owner operation."""
    if config.resume_validation_policy != "strict":
        raise ResumeUnsupportedError("typed tick resume requires strict admission")
    if (
        state is not None
        and state.metadata.get("realtime_resume_boundary") != "committed-parent-bar-v1"
    ):
        raise ResumeUnsupportedError("typed tick resume requires a committed parent-bar boundary")
    if state is not None and (state.bar_index < 0 or state.bar_index >= len(series)):
        raise ResumeUnsupportedError("typed tick cursor must reference a committed input bar")
    if config.realtime_tick_provider is not None or type(config.realtime_ticks) not in (
        list,
        tuple,
    ):
        raise ResumeUnsupportedError("typed tick resume requires explicit list/tuple Tick input")
    owner = getattr(strategy_class, "realtime_resume_runtime", None)
    if owner not in ("config", "strategy"):
        raise ResumeUnsupportedError(
            "typed tick strategy must declare its config or strategy runtime owner"
        )
    owners = [("strategy_state", strategy_class, None if state is None else state.strategy_state)]
    marker = None if state is None else state.metadata.get("realtime_runtime_owner")
    if owner == "strategy":
        if config.runtime is not None or (state is not None and state.runtime_state is not None):
            raise ResumeUnsupportedError(
                "strategy-owned tick runtime cannot have an external owner"
            )
        if state is not None and marker != "strategy-checkpoint-v1":
            raise ResumeUnsupportedError(
                "tick checkpoint runtime owner differs from selected strategy"
            )
        if not callable(getattr(strategy_class, "_commit_bar", None)):
            raise ResumeUnsupportedError(
                "strategy-owned tick runtime needs a required bar commit hook"
            )
    else:
        if marker is not None:
            raise ResumeUnsupportedError("tick checkpoint runtime owner differs from config owner")
        owners.insert(
            0,
            ("runtime_state", type(config.runtime), None if state is None else state.runtime_state),
        )
    validators: list[tuple[str, Any, object]] = []
    for label, cls, payload in owners:
        if state is not None and payload is None:
            raise ResumeUnsupportedError("typed tick resume is missing " + label)
        descriptor = inspect.getattr_static(cls, "validate_resume_state", None)
        if not isinstance(descriptor, (staticmethod, classmethod)):
            raise ResumeUnsupportedError(
                label + " owner needs a static/class validate_resume_state"
            )
        validators.append((label, getattr(cls, "validate_resume_state"), payload))
    try:
        _require_rollback_state(strategy_class, config.runtime)
    except Exception as error:
        raise ResumeUnsupportedError("typed tick owner rollback contract is incomplete") from error
    if state is not None:
        committed_bar = series.get_bar(state.bar_index)
        for label, validate, payload in validators:
            try:
                result = validate(
                    clone_state(payload), bar_index=state.bar_index, committed_bar=committed_bar
                )
                if result is not None:
                    raise ValueError("pure owner validator must return None")
            except Exception as error:
                raise ResumeUnsupportedError(
                    label + " owner preflight failed: " + str(error)
                ) from error
    # Preserve the config identity's original list/tuple kind, then freeze input
    # only for resolution. Reuse this admitted immutable schedule during execution.
    schedule = (
        admitted_schedule
        if admitted_schedule is not None
        else admit_explicit_tick_schedule(config, series)
    )
    if state is not None:
        expected = state.metadata.get("realtime_tick_schedule_fingerprint")
        actual = realtime_tick_schedule_fingerprint(schedule[: state.bar_index + 1])
        if expected != actual:
            raise ResumeUnsupportedError(
                "typed tick schedule fingerprint does not match processed ticks"
            )
    return schedule
