from __future__ import annotations

from dataclasses import asdict
from typing import Any, Sequence

from .base import BackendBarResult, BackendExecutionResult


def _pinelib_imports() -> dict[str, Any]:
    try:
        from pinelib.backtest import run_generated_strategy
        from pinelib.core import PineRuntime
        from pinelib.core.bar import Bar as PineBar
        from pinelib.core.types import RuntimeConfig, SymbolInfo, TimeframeInfo
        from pinelib.strategy import StrategyContext
    except ImportError as exc:  # pragma: no cover - exercised when optional dep is absent
        raise ImportError("PineRuntimeBackend requires pinelib installed") from exc
    return {
        "run_generated_strategy": run_generated_strategy,
        "PineRuntime": PineRuntime,
        "PineBar": PineBar,
        "RuntimeConfig": RuntimeConfig,
        "SymbolInfo": SymbolInfo,
        "TimeframeInfo": TimeframeInfo,
        "StrategyContext": StrategyContext,
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
    """Official BacktestEngine bridge to ast2python-generated Pine strategies.

    BacktestEngine owns data/window orchestration. This backend owns only the
    handoff into pinelib's PineRuntime + StrategyContext execution stack.
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

        runtime_config = imports["RuntimeConfig"](
            strict_tv_parity=getattr(config, "parity_mode", None) == "strict",
            process_orders_on_close=getattr(config, "process_orders_on_close", None),
            calc_on_order_fills=getattr(config, "calc_on_order_fills", None),
            calc_on_every_tick=getattr(config, "calc_on_every_tick", None),
        )
        symbol_info = imports["SymbolInfo"](
            tickerid=runtime_kwargs.pop("symbol", getattr(config, "symbol", "UNKNOWN")),
            mintick=getattr(config, "mintick", None) or 0.01,
            currency=getattr(config, "currency", None),
        )
        timeframe = imports["TimeframeInfo"].from_string(
            runtime_kwargs.pop("timeframe", getattr(config, "timeframe", "1"))
        )
        runtime = imports["PineRuntime"](
            symbol_info=symbol_info,
            timeframe=timeframe,
            data_provider=runtime_kwargs.pop("data_provider", getattr(config, "data_provider", None)),
            config=runtime_config,
            **runtime_kwargs,
        )

        try:
            strategy = strategy_class(params=params, runtime=runtime)
        except TypeError:
            strategy = strategy_class(params, runtime)

        pine_bars = [_bar_to_pinelib(bar, imports["PineBar"]) for bar in bars]

        if is_indicator:
            # Indicators: run bar-by-bar so CLI callers can report progress.
            for idx, bar in enumerate(pine_bars):
                runtime.begin_bar(bar)
                try:
                    strategy._process_bar(bar)
                finally:
                    runtime.end_bar()
                if progress_callback is not None:
                    progress_callback(idx + 1, len(pine_bars))
            bar_results: list[BackendBarResult] = []
            plots = None
            plot_recorder = getattr(runtime, "plot_recorder", None)
            if plot_recorder is not None:
                get_records = getattr(plot_recorder, "get_records", None)
                plots = get_records() if callable(get_records) else plot_recorder
            return BackendExecutionResult(
                bar_results=bar_results,
                trades=[],
                plots=plots,
                diagnostics={
                    "runtime_diagnostics": list(getattr(runtime.config, "diagnostics", []) or []),
                    "backend": self.name,
                },
                raw_context=None,
                raw_result=None,
            )

        # Strategy path (existing behavior)
        strategy_ctx = getattr(strategy, "ctx", None)
        if not isinstance(strategy_ctx, imports["StrategyContext"]):
            strategy_ctx = imports["StrategyContext"](
                initial_capital=getattr(config, "initial_capital", 100000.0),
                currency=getattr(config, "currency", "USD"),
                default_qty_type=getattr(config, "default_qty_type", "fixed"),
                default_qty_value=getattr(config, "default_qty_value", 1.0),
                pyramiding=getattr(config, "pyramiding", 1),
                commission_type=getattr(config, "commission_type", "percent"),
                commission_value=getattr(config, "commission_value", 0.0),
                slippage=getattr(config, "slippage", 0.0),
                process_orders_on_close=getattr(config, "process_orders_on_close", False),
                calc_on_order_fills=getattr(config, "calc_on_order_fills", False),
                calc_on_every_tick=getattr(config, "calc_on_every_tick", False),
                use_bar_magnifier=getattr(config, "use_bar_magnifier", False),
                margin_long=getattr(config, "margin_long", 100.0),
                margin_short=getattr(config, "margin_short", 100.0),
                qty_step=getattr(config, "qty_step", None),
                qty_rounding_mode=getattr(config, "qty_rounding_mode", getattr(config, "qty_rounding", "none")),
            )
            setattr(strategy, "ctx", strategy_ctx)
        else:
            _sync_strategy_context_from_config(strategy_ctx, config)
        strategy_ctx.attach_runtime(runtime)

        result = imports["run_generated_strategy"](
            strategy,
            runtime,
            strategy_ctx,
            pine_bars,
            progress_callback=progress_callback,
        )

        bar_results = []
        for idx, snapshot in enumerate(result.snapshots):
            phase = "prehistory" if idx < max(0, effective_pre_bars) else "score"
            raw = asdict(snapshot)
            bar_results.append(
                BackendBarResult(
                    time=int(snapshot.time),
                    phase=phase,
                    equity=float(snapshot.equity),
                    netprofit=float(snapshot.netprofit),
                    openprofit=float(snapshot.openprofit),
                    position_size=float(snapshot.position_size),
                    position_avg_price=float(snapshot.position_avg_price),
                    closedtrades=int(snapshot.closedtrades),
                    raw=raw,
                )
            )

        plots = None
        plot_recorder = getattr(runtime, "plot_recorder", None)
        if plot_recorder is not None:
            get_records = getattr(plot_recorder, "get_records", None)
            plots = get_records() if callable(get_records) else plot_recorder

        return BackendExecutionResult(
            bar_results=bar_results,
            trades=list(getattr(strategy_ctx, "closed_trade_log", []) or []),
            plots=plots,
            diagnostics={
                "runtime_diagnostics": list(getattr(runtime.config, "diagnostics", []) or []),
                "backend": self.name,
            },
            raw_context=strategy_ctx,
            raw_result=result,
        )
