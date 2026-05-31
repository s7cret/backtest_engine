from __future__ import annotations

from typing import Any, Sequence

from .base import BackendExecutionResult


class UnsupportedPineRuntimeBackendMode(RuntimeError):
    """Raised when the legacy PineRuntime backend is used for broker execution."""


def _pinelib_imports() -> dict[str, Any]:
    try:
        from pinelib.core import PineRuntime
        from pinelib.core.bar import Bar as PineBar
        from pinelib.core.types import RuntimeConfig, SymbolInfo, TimeframeInfo
    except ImportError as exc:  # pragma: no cover - exercised when optional dep is absent
        raise ImportError("PineRuntimeBackend requires pinelib installed") from exc
    return {
        "PineRuntime": PineRuntime,
        "PineBar": PineBar,
        "RuntimeConfig": RuntimeConfig,
        "SymbolInfo": SymbolInfo,
        "TimeframeInfo": TimeframeInfo,
    }


def _bar_to_pinelib(bar: Any, pine_bar_type: type) -> Any:
    bar_time = getattr(bar, "time", getattr(bar, "timestamp", 0))
    bar_time_close = getattr(bar, "time_close", getattr(bar, "close_time_ms", None))
    return pine_bar_type(
        time=int(bar_time),
        open=float(getattr(bar, "open")),
        high=float(getattr(bar, "high")),
        low=float(getattr(bar, "low")),
        close=float(getattr(bar, "close")),
        volume=0.0 if getattr(bar, "volume", None) is None else float(getattr(bar, "volume")),
        time_close=bar_time_close,
    )


def _sync_strategy_context_from_config(strategy_ctx: Any, config: Any) -> None:
    """Apply engine config to generated strategies that already own a context."""
    mappings = {
        "initial_capital": getattr(config, "initial_capital", None),
        "currency": getattr(config, "currency", None),
        "default_qty_type": getattr(config, "default_qty_type", None),
        "default_qty_value": getattr(config, "default_qty_value", None),
        "pyramiding": getattr(config, "pyramiding", None),
        "commission_type": getattr(config, "commission_type", None),
        "commission_value": getattr(config, "commission_value", None),
        "slippage": getattr(config, "slippage", None),
        "process_orders_on_close": getattr(config, "process_orders_on_close", None),
        "calc_on_order_fills": getattr(config, "calc_on_order_fills", None),
        "calc_on_every_tick": getattr(config, "calc_on_every_tick", None),
        "use_bar_magnifier": getattr(config, "use_bar_magnifier", None),
        "margin_long": getattr(config, "margin_long", None),
        "margin_short": getattr(config, "margin_short", None),
        "qty_step": getattr(config, "qty_step", None),
        "qty_rounding_mode": getattr(config, "qty_rounding_mode", getattr(config, "qty_rounding", None)),
    }
    for name, value in mappings.items():
        if value is None:
            continue
        if hasattr(strategy_ctx, name):
            setattr(strategy_ctx, name, value)
        declaration = getattr(strategy_ctx, "declaration", None)
        if declaration is not None and hasattr(declaration, name):
            setattr(declaration, name, value)


class PineRuntimeBackend:
    """PineRuntime handoff backend for generated indicators.

    Strategy/broker execution must use ``backtest_engine.adapters.generated_strategy``
    so BacktestEngine remains the sole fill/trade ledger authority.
    """

    name = "pine_runtime"

    def execute(
        self,
        strategy_class: type | Any,
        bars: Sequence[Any],
        *,
        config: Any,
        execution_window: Any,
        effective_pre_bars: int = 0,
        runtime_kwargs: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        is_indicator: bool = False,
    ) -> BackendExecutionResult:
        imports = _pinelib_imports()
        runtime_kwargs = dict(runtime_kwargs or {})
        params = dict(params or {})
        progress_callback = runtime_kwargs.pop("progress_callback", None)
        plot_from_ms = runtime_kwargs.pop("plot_from_ms", None)
        plot_to_ms = runtime_kwargs.pop("plot_to_ms", None)

        runtime_config = imports["RuntimeConfig"](
            strict_tv_parity=getattr(config, "parity_mode", None) == "strict",
            process_orders_on_close=getattr(config, "process_orders_on_close", None),
            calc_on_order_fills=getattr(config, "calc_on_order_fills", None),
            calc_on_every_tick=getattr(config, "calc_on_every_tick", None),
            extra={
                "exchange": getattr(config, "exchange", None),
                "market_type": getattr(config, "market_type", None),
            },
        )
        symbol_info = imports["SymbolInfo"](
            tickerid=runtime_kwargs.pop("symbol", getattr(config, "symbol", "UNKNOWN")),
            mintick=getattr(config, "mintick", None) or 0.01,
            currency=getattr(config, "currency", None),
            exchange=getattr(config, "exchange", None),
        )
        timeframe = imports["TimeframeInfo"].from_string(
            runtime_kwargs.pop("timeframe", getattr(config, "timeframe", "1"))
        )
        runtime = imports["PineRuntime"](
            symbol_info=symbol_info,
            timeframe=timeframe,
            data_provider=runtime_kwargs.pop("data_provider", None),
            config=runtime_config,
            **runtime_kwargs,
        )
        plot_recorder = getattr(runtime, "plot_recorder", None)
        set_time_window = getattr(plot_recorder, "set_time_window", None)
        if callable(set_time_window):
            set_time_window(plot_from_ms, plot_to_ms)

        try:
            strategy = strategy_class(params=params, runtime=runtime)
        except TypeError:
            strategy = strategy_class(params, runtime)

        pine_bars = [_bar_to_pinelib(bar, imports["PineBar"]) for bar in bars]
        if pine_bars:
            runtime.request_data_end_ms = pine_bars[-1].time_close or pine_bars[-1].time

        if not is_indicator:
            raise UnsupportedPineRuntimeBackendMode(
                "PineRuntimeBackend no longer runs generated strategy broker paths; "
                "wrap generated strategy classes with make_generated_strategy_adapter(...) "
                "and run them through BacktestEngine native execution"
            )

        # Indicators: run bar-by-bar so CLI callers can report progress.
        for idx, bar in enumerate(pine_bars):
            runtime.begin_bar(bar)
            try:
                strategy._process_bar(bar)
            finally:
                runtime.end_bar()
            if progress_callback is not None:
                progress_callback(idx + 1, len(pine_bars))
        plots = None
        plot_recorder = getattr(runtime, "plot_recorder", None)
        if plot_recorder is not None:
            get_records = getattr(plot_recorder, "get_records", None)
            plots = get_records() if callable(get_records) else plot_recorder

        return BackendExecutionResult(
            bar_results=[],
            trades=[],
            plots=plots,
            diagnostics={
                "runtime_diagnostics": list(getattr(runtime.config, "diagnostics", []) or []),
                "backend": self.name,
            },
            raw_context=None,
            raw_result=None,
        )
