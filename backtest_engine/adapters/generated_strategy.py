"""Bridge AST2Python/PineLib generated strategies into BacktestEngine.

This module is optional: importing :mod:`backtest_engine` or
:mod:`backtest_engine.adapters.generated_strategy` does not import PineLib or
AST2Python. PineLib is imported only when an adapter class is constructed.

The bridge intentionally delegates order execution to BacktestEngine. Generated
strategy code still uses a PineRuntime for Pine series/builtins, but strategy
order calls are redirected to the engine StrategyContext. Unsupported PineLib
broker-owned semantics fail closed instead of being approximated silently.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol, TypeVar

from backtest_engine.context import StrategyContext as EngineStrategyContext
from backtest_engine.models import Bar as EngineBar


from backtest_engine.adapters.generated_strategy_context import (
    _BridgeScalarSeries,
    _BridgeStrategyContext,
    _direction,
    _none_if_pine_na,
    _pine_na,
)
from backtest_engine.adapters.generated_strategy_errors import (
    GeneratedStrategyBridgeError,
    UnsupportedGeneratedStrategySemantics,
)


class GeneratedStrategyClass(Protocol):
    def __call__(
        self, params: dict[str, Any] | None = None, runtime: Any | None = None
    ) -> Any: ...


TGeneratedStrategyClass = TypeVar(
    "TGeneratedStrategyClass", bound=GeneratedStrategyClass
)


@dataclass(frozen=True, slots=True)
class GeneratedStrategyAdapterOptions:
    """Options for :func:`make_generated_strategy_adapter`.

    ``fail_on_*`` defaults are intentionally conservative. BacktestEngine has
    its own support for these features, but the bridge cannot safely infer that
    a generated PineLib declaration and engine config are identical unless the
    caller configured both deliberately.
    """

    symbol: str = "TEST"
    timeframe: str = "1"
    timezone: str = "UTC"
    data_provider: Any | None = None
    intrabar_provider: Any | None = None
    fail_on_calc_on_order_fills: bool = True
    fail_on_calc_on_every_tick: bool = True
    fail_on_bar_magnifier: bool = True
    fail_on_nonstandard_margin: bool = True
    fail_on_config_mismatch: bool = True


def make_generated_strategy_adapter(
    generated_strategy_class: TGeneratedStrategyClass,
    *,
    options: GeneratedStrategyAdapterOptions | None = None,
) -> type:
    """Return a BacktestEngine strategy class for an AST2Python strategy class.

    The returned class is passed directly to ``BacktestEngine.run``. It keeps a
    PineRuntime for generated series/builtin behavior, replaces the generated
    PineLib StrategyContext with a bridge context, and forwards order intents to
    BacktestEngine's StrategyContext.
    """

    adapter_options = options or GeneratedStrategyAdapterOptions()

    class BacktestGeneratedStrategyAdapter:
        generated_strategy_class_ref = generated_strategy_class

        def __init__(
            self,
            params: dict[str, Any] | None = None,
            runtime: Any | None = None,
            ctx: EngineStrategyContext | None = None,
        ) -> None:
            del runtime
            if ctx is None:
                raise GeneratedStrategyBridgeError(
                    "BacktestEngine StrategyContext is required"
                )
            self.ctx = ctx
            self._pine_runtime = _make_pine_runtime(adapter_options)
            data_provider = (
                getattr(self.__class__, "runtime_data_provider", None)
                or adapter_options.data_provider
            )
            intrabar_provider = (
                getattr(self.__class__, "runtime_intrabar_provider", None)
                or adapter_options.intrabar_provider
            )
            if data_provider is not None:
                self._pine_runtime.data_provider = data_provider
            if intrabar_provider is not None:
                self._pine_runtime.intrabar_provider = intrabar_provider
            self._pine_runtime.config.extra["max_bars_back"] = int(
                getattr(ctx.config, "max_bars_back", 0) or 0
            )
            request_data_end_ms = getattr(
                self.__class__, "runtime_request_data_end_ms", None
            )
            if request_data_end_ms is not None:
                self._pine_runtime.request_data_end_ms = int(request_data_end_ms)
            plot_recorder = getattr(self._pine_runtime, "plot_recorder", None)
            set_time_window = getattr(plot_recorder, "set_time_window", None)
            if callable(set_time_window):
                if not bool(getattr(self.__class__, "runtime_capture_plots", True)):
                    set_time_window(1, 0)
                else:
                    set_time_window(
                        getattr(self.__class__, "runtime_plot_from_ms", None),
                        getattr(self.__class__, "runtime_plot_to_ms", None),
                    )
            self.generated = generated_strategy_class(
                params=params, runtime=self._pine_runtime
            )
            self._validate_generated_declaration(
                getattr(self.generated, "ctx", None), adapter_options, ctx.config
            )
            self._bridge_ctx = _BridgeStrategyContext(ctx)
            self._bridge_ctx.attach_runtime(self._pine_runtime)
            self.generated.ctx = self._bridge_ctx
            self._active_engine_bar_index: int | None = None

        def _process_bar(self, bar: EngineBar, bar_index: int) -> None:
            fixed_timeframe_ms = getattr(
                getattr(self._pine_runtime, "timeframe", None), "interval_ms", None
            )
            pine_bar = _to_pine_bar(bar, fixed_timeframe_ms=fixed_timeframe_ms)
            if self._active_engine_bar_index != bar_index:
                if self._active_engine_bar_index is not None:
                    self._pine_runtime.end_bar()
                self._pine_runtime.begin_bar(pine_bar)
                plot_from_ms = getattr(self.__class__, "runtime_plot_from_ms", None)
                plot_to_ms = getattr(self.__class__, "runtime_plot_to_ms", None)
                is_visible = (
                    plot_from_ms is None or pine_bar.time >= plot_from_ms
                ) and (plot_to_ms is None or pine_bar.time < plot_to_ms)
                is_last_visible = bool(
                    is_visible
                    and plot_to_ms is not None
                    and (pine_bar.time_close or pine_bar.time) >= plot_to_ms
                )
                self._pine_runtime.barstate = replace(
                    self._pine_runtime.barstate,
                    islast=is_last_visible,
                    ishistory=not is_last_visible,
                    isrealtime=is_last_visible,
                    isnew=not is_last_visible,
                    isconfirmed=not is_last_visible,
                )
                self._active_engine_bar_index = bar_index
            self._bridge_ctx._sync_from_engine()
            process = getattr(self.generated, "_process_bar", None)
            if not callable(process):
                raise GeneratedStrategyBridgeError(
                    "generated strategy must expose _process_bar(bar)"
                )
            process(pine_bar)
            current_index = self._pine_runtime.bar_index + 1
            if current_index != bar_index:
                raise GeneratedStrategyBridgeError(
                    f"PineRuntime/BacktestEngine bar index mismatch: {current_index} != {bar_index}"
                )
            self._bridge_ctx._commit_scalar_history()

        def _finalize(self) -> None:
            if self._active_engine_bar_index is not None:
                self._pine_runtime.end_bar()
                self._active_engine_bar_index = None

        @staticmethod
        def _validate_generated_declaration(
            generated_ctx: Any,
            opts: GeneratedStrategyAdapterOptions,
            engine_config: Any,
        ) -> None:
            declaration = getattr(generated_ctx, "declaration", None)
            if declaration is None:
                return
            if (
                opts.fail_on_calc_on_order_fills
                and bool(getattr(declaration, "calc_on_order_fills", False))
                and not bool(getattr(engine_config, "calc_on_order_fills", False))
            ):
                raise UnsupportedGeneratedStrategySemantics(
                    "calc_on_order_fills declaration requires BacktestConfig.calc_on_order_fills=True"
                )
            if opts.fail_on_calc_on_every_tick and bool(
                getattr(declaration, "calc_on_every_tick", False)
            ):
                raise UnsupportedGeneratedStrategySemantics(
                    "calc_on_every_tick generated semantics require explicit tick data scheduling"
                )
            if opts.fail_on_bar_magnifier and bool(
                getattr(declaration, "use_bar_magnifier", False)
            ):
                raise UnsupportedGeneratedStrategySemantics(
                    "use_bar_magnifier generated semantics require explicit lower-timeframe bridge"
                )
            if opts.fail_on_nonstandard_margin and (
                float(getattr(declaration, "margin_long", 100.0)) != 100.0
                or float(getattr(declaration, "margin_short", 100.0)) != 100.0
            ):
                raise UnsupportedGeneratedStrategySemantics(
                    "non-standard generated margin settings are not adapted"
                )
            if opts.fail_on_config_mismatch:
                diff = _declaration_config_diff(declaration, engine_config)
                if diff:
                    raise UnsupportedGeneratedStrategySemantics(
                        "generated declaration/config mismatch: " + repr(diff)
                    )

    BacktestGeneratedStrategyAdapter.__name__ = f"Backtest{getattr(generated_strategy_class, '__name__', 'GeneratedStrategy')}Adapter"
    BacktestGeneratedStrategyAdapter.__qualname__ = (
        BacktestGeneratedStrategyAdapter.__name__
    )
    return BacktestGeneratedStrategyAdapter


def _declaration_config_diff(
    declaration: Any, engine_config: Any
) -> dict[str, dict[str, Any]]:
    field_pairs = {
        "initial_capital": "initial_capital",
        "default_qty_type": "default_qty_type",
        "default_qty_value": "default_qty_value",
        "pyramiding": "pyramiding",
        "commission_type": "commission_type",
        "commission_value": "commission_value",
        "slippage": "slippage",
        "process_orders_on_close": "process_orders_on_close",
        "close_entries_rule": "exit_matching",
        "margin_long": "margin_long",
        "margin_short": "margin_short",
        "calc_on_order_fills": "calc_on_order_fills",
        "calc_on_every_tick": "calc_on_every_tick",
        "use_bar_magnifier": "use_bar_magnifier",
    }
    diff: dict[str, dict[str, Any]] = {}
    for decl_field, cfg_field in field_pairs.items():
        if not hasattr(declaration, decl_field):
            continue
        expected = getattr(declaration, decl_field)
        actual = getattr(engine_config, cfg_field, None)
        if decl_field == "commission_type":
            expected = {
                "cash_per_order": "fixed_per_order",
                "cash_per_contract": "fixed_per_contract",
            }.get(expected, expected)
        if expected != actual:
            diff[decl_field] = {"declaration": expected, "config": actual}
    return diff


def _make_pine_runtime(options: GeneratedStrategyAdapterOptions) -> Any:
    try:
        from pinelib.core import PineRuntime
        from pinelib.core.types import RuntimeConfig, SymbolInfo, TimeframeInfo
    except ImportError as exc:  # pragma: no cover - depends on optional install state
        raise GeneratedStrategyBridgeError(
            "PineLib is required to run generated strategy adapters"
        ) from exc
    return PineRuntime(
        symbol_info=SymbolInfo(tickerid=options.symbol, timezone=options.timezone),
        timeframe=TimeframeInfo.from_string(options.timeframe),
        config=RuntimeConfig(extra={"record_lower_tf_metadata": False}),
    )


def _pine_timestamp(value: int | None) -> int | None:
    if value is None:
        return None
    # BacktestEngine bar feeds may use Unix seconds, while Pine runtime/Pine
    # builtins expose `time`/`time_close` in milliseconds. Convert seconds at
    # the bridge boundary so session/timezone functions evaluate on real dates.
    return int(value) * 1000 if abs(int(value)) < 10_000_000_000 else int(value)


def _normalize_pine_time_close(
    time_close: int | None,
    *,
    open_time: int,
    fixed_timeframe_ms: int | None,
) -> int | None:
    if time_close is None:
        return None
    if (
        fixed_timeframe_ms is not None
        and int(time_close) == int(open_time) + fixed_timeframe_ms - 1
    ):
        return int(open_time) + fixed_timeframe_ms
    return int(time_close)


def _to_pine_bar(bar: EngineBar, *, fixed_timeframe_ms: int | None = None) -> Any:
    try:
        from pinelib.core import Bar as PineBar
    except ImportError as exc:  # pragma: no cover - depends on optional install state
        raise GeneratedStrategyBridgeError(
            "PineLib is required to convert bars for generated strategy adapters"
        ) from exc
    return PineBar(
        time=_pine_timestamp(bar.time),
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=0.0 if bar.volume is None else bar.volume,
        time_close=_pine_timestamp(
            _normalize_pine_time_close(
                getattr(bar, "time_close", None),
                open_time=bar.time,
                fixed_timeframe_ms=fixed_timeframe_ms,
            )
        ),
    )


__all__ = [
    "GeneratedStrategyAdapterOptions",
    "GeneratedStrategyBridgeError",
    "UnsupportedGeneratedStrategySemantics",
    "make_generated_strategy_adapter",
    "_BridgeScalarSeries",
    "_direction",
    "_none_if_pine_na",
    "_pine_na",
]
