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
    return pine_bar_type(
        time=int(getattr(bar, "time")),
        open=float(getattr(bar, "open")),
        high=float(getattr(bar, "high")),
        low=float(getattr(bar, "low")),
        close=float(getattr(bar, "close")),
        volume=0.0 if getattr(bar, "volume", None) is None else float(getattr(bar, "volume")),
        time_close=getattr(bar, "time_close", None),
    )


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
    ) -> BackendExecutionResult:
        imports = _pinelib_imports()
        runtime_kwargs = dict(runtime_kwargs or {})
        params = dict(params or {})

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
            )
            setattr(strategy, "ctx", strategy_ctx)
        strategy_ctx.attach_runtime(runtime)

        pine_bars = [_bar_to_pinelib(bar, imports["PineBar"]) for bar in bars]
        result = imports["run_generated_strategy"](strategy, runtime, strategy_ctx, pine_bars)

        bar_results: list[BackendBarResult] = []
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
