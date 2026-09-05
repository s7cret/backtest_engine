"""Describe callbacks when the host has an admitted chart dataset.

Low-level legacy callback probes have no dataset bounds and retain their old
run_bar path; they must not fabricate last-bar flags for generated Pine code.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from openpine_contracts import ExecutionEvent, decimal_string

from backtest_engine.errors import StrategyRuntimeError
from backtest_engine.models import Bar


def describe_execution(
    engine: Any, bar: Bar, index: int, *, fill_cause: bool
) -> ExecutionEvent | None:
    if engine._execution_last_bar_index < 0:
        return None
    realtime = bool(getattr(engine, "_realtime_tick_execution", False))
    engine._execution_callback_sequence += 1
    fill = engine.fills[-1] if fill_cause and engine.fills else None
    if fill_cause and fill is None:
        raise StrategyRuntimeError("fill recalculation has no causal fill")
    return ExecutionEvent(
        sequence=engine._execution_callback_sequence,
        bar_index=index,
        last_bar_index=index if realtime else engine._execution_last_bar_index,
        last_historical_bar_index=-1 if realtime else engine._execution_last_bar_index,
        bar_open_time_utc_ms=int(bar.time),
        phase="ORDER_FILL_RECALC"
        if fill_cause
        else ("REALTIME_EVAL" if realtime else "HISTORICAL_EVAL"),
        realtime=realtime,
        final_tick=bool(getattr(engine, "_execution_final_tick", True)) if realtime else True,
        tick_index=int(getattr(engine, "_execution_tick_index", 0)) if realtime else 0,
        recalc_iteration=engine._strategy_callback_recalc_iteration,
        cause="ORDER_FILL" if fill_cause else ("TICK" if realtime else "BAR_CLOSE"),
        fill_order_id=str(fill.order_id) if fill is not None else None,
        fill_price=decimal_string(Decimal(repr(float(fill.price)))) if fill is not None else None,
    )
