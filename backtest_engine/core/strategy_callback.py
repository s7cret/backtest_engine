"""One callback boundary shared by native Pine adapters and legacy strategies."""

from __future__ import annotations

from typing import Any

from backtest_engine.core.protocol_boundary import emit_protocol_bar_begin
from backtest_engine.core.strategy_projection import (
    compact_broker_projection,
    compact_ledger_projection,
)
from backtest_engine.errors import BacktestEngineError, StrategyRuntimeError
from backtest_engine.models import Bar


def call_strategy(
    engine: Any, strategy: Any, bar: Bar, i: int, *, fill_cause: bool = False
) -> None:
    if engine._strategy_callback_bar_index != i:
        engine._strategy_callback_bar_index = i
        engine._strategy_callback_recalc_iteration = 0
    else:
        engine._strategy_callback_recalc_iteration += 1
    from backtest_engine.core.execution_event import describe_execution

    event = describe_execution(engine, bar, i, fill_cause=fill_cause)
    engine._execution_event = event
    emit_protocol_bar_begin(engine, bar, i)
    callback = getattr(engine.callbacks, "on_strategy_callback", None)
    if callback is None and engine.callbacks.extra is not None:
        callback = engine.callbacks.extra.get("on_strategy_callback")
    if callback:
        projection = engine.export_strategy_ledger_projection()
        engine._cb(
            "on_strategy_callback",
            {
                "callback": "strategy",
                "execution_event": None if event is None else event.to_dict(),
                "bar_index": i,
                "bar_open_time_utc_ms": int(bar.time),
                "phase": engine._current_phase,
                "recalc_iteration": engine._strategy_callback_recalc_iteration,
                "projection": projection,
                "broker": compact_broker_projection(projection),
                "ledger": compact_ledger_projection(projection),
            },
        )
    try:
        run_callback = getattr(strategy, "run_callback", None)
        run_bar = getattr(strategy, "run_bar", None)
        if callable(run_callback):
            if event is None:
                raise StrategyRuntimeError("run_callback requires explicit chart dataset bounds")
            run_callback(bar, event)
        elif callable(run_bar):
            run_bar(bar, i)
        else:
            strategy._process_bar(bar, i)
    except BacktestEngineError:
        raise
    except Exception as e:
        raise StrategyRuntimeError(str(e)) from e
