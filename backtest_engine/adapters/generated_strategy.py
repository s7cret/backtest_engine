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

from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from backtest_engine.context import StrategyContext as EngineStrategyContext
from backtest_engine.models import Bar as EngineBar


class GeneratedStrategyBridgeError(RuntimeError):
    """Raised when a generated strategy cannot be safely adapted."""


class UnsupportedGeneratedStrategySemantics(GeneratedStrategyBridgeError):
    """Raised for generated/PineLib semantics this bridge will not approximate."""


class GeneratedStrategyClass(Protocol):
    def __call__(self, params: dict[str, Any] | None = None, runtime: Any | None = None) -> Any: ...


TGeneratedStrategyClass = TypeVar("TGeneratedStrategyClass", bound=GeneratedStrategyClass)


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
    fail_on_calc_on_order_fills: bool = True
    fail_on_calc_on_every_tick: bool = True
    fail_on_bar_magnifier: bool = True
    fail_on_nonstandard_margin: bool = True


class _BridgeStrategyContext:
    def __init__(self, engine_ctx: EngineStrategyContext) -> None:
        self._engine_ctx = engine_ctx
        self._runtime: Any | None = None
        self._sync_from_engine()

    def attach_runtime(self, runtime: Any) -> None:
        self._runtime = runtime
        runtime.strategy = self

    def _sync_from_engine(self) -> None:
        state = self._engine_ctx.state
        self.equity = float(state.equity)
        self.netprofit = float(state.net_profit)
        self.openprofit = float(state.open_profit)
        self.grossprofit = float(state.gross_profit)
        self.grossloss = float(state.gross_loss)
        self.position_size = float(state.position_size)
        self.position_avg_price = float(state.position_avg_price or 0.0)
        self.opentrades = int(state.open_trades)
        self.closedtrades = int(state.closed_trades)
        self.max_drawdown = float(state.max_drawdown)
        self.max_runup = 0.0
        closed = getattr(state, "_closed_trades_ref", [])
        self.wintrades = sum(1 for trade in closed if trade.profit > 0)
        self.losstrades = sum(1 for trade in closed if trade.profit < 0)
        self.eventrades = sum(1 for trade in closed if trade.profit == 0)

    def entry(
        self,
        id: str,
        direction: str,
        qty: float | None = None,
        limit: float | None = None,
        stop: float | None = None,
        *,
        source_map: object | None = None,
    ) -> None:
        del source_map
        self._engine_ctx.entry(
            id=id, direction=_direction(direction), qty=qty, limit=limit, stop=stop
        )

    def order(
        self,
        id: str,
        direction: str,
        qty: float | None = None,
        limit: float | None = None,
        stop: float | None = None,
        oca_name: str | None = None,
        oca_type: str | None = None,
        *,
        source_map: object | None = None,
    ) -> None:
        del source_map
        self._engine_ctx.order(
            id=id,
            direction=_direction(direction),
            qty=qty,
            limit=limit,
            stop=stop,
            oca_name=oca_name,
            oca_type=oca_type,
        )

    def exit(
        self,
        id: str,
        from_entry: str | None = None,
        qty: float | None = None,
        qty_percent: float | None = None,
        limit: float | None = None,
        stop: float | None = None,
        profit: float | None = None,
        loss: float | None = None,
        trail_price: float | None = None,
        trail_points: float | None = None,
        trail_offset: float | None = None,
        *,
        source_map: object | None = None,
    ) -> None:
        del source_map
        self._engine_ctx.exit(
            id=id,
            from_entry=from_entry,
            qty=qty,
            qty_percent=qty_percent,
            limit=limit,
            stop=stop,
            profit=profit,
            loss=loss,
            trail_price=trail_price,
            trail_points=trail_points,
            trail_offset=trail_offset,
        )

    def close(
        self,
        id: str,
        qty: float | None = None,
        qty_percent: float | None = None,
        immediately: bool = False,
        *,
        source_map: object | None = None,
    ) -> None:
        del source_map
        self._engine_ctx.close(id=id, qty=qty, qty_percent=qty_percent, immediately=immediately)

    def close_all(self, immediately: bool = False, *, source_map: object | None = None) -> None:
        del source_map
        self._engine_ctx.close_all(immediately=immediately)

    def cancel(self, id: str, *, source_map: object | None = None) -> None:
        del source_map
        self._engine_ctx.cancel(id)

    def cancel_all(self, *, source_map: object | None = None) -> None:
        del source_map
        self._engine_ctx.cancel_all()

    def accept_orders_from_generated_code(self) -> None:
        return None


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
                raise GeneratedStrategyBridgeError("BacktestEngine StrategyContext is required")
            self.ctx = ctx
            self._pine_runtime = _make_pine_runtime(adapter_options)
            self.generated = generated_strategy_class(params=params, runtime=self._pine_runtime)
            self._validate_generated_declaration(
                getattr(self.generated, "ctx", None), adapter_options
            )
            self._bridge_ctx = _BridgeStrategyContext(ctx)
            self._bridge_ctx.attach_runtime(self._pine_runtime)
            self.generated.ctx = self._bridge_ctx

        def _process_bar(self, bar: EngineBar, bar_index: int) -> None:
            pine_bar = _to_pine_bar(bar)
            self._pine_runtime.begin_bar(pine_bar)
            try:
                self._bridge_ctx._sync_from_engine()
                process = getattr(self.generated, "_process_bar", None)
                if not callable(process):
                    raise GeneratedStrategyBridgeError(
                        "generated strategy must expose _process_bar(bar)"
                    )
                process(pine_bar)
            finally:
                self._pine_runtime.end_bar()
            if self._pine_runtime.bar_index != bar_index:
                raise GeneratedStrategyBridgeError(
                    f"PineRuntime/BacktestEngine bar index mismatch: {self._pine_runtime.bar_index} != {bar_index}"
                )

        @staticmethod
        def _validate_generated_declaration(
            generated_ctx: Any, opts: GeneratedStrategyAdapterOptions
        ) -> None:
            declaration = getattr(generated_ctx, "declaration", None)
            if declaration is None:
                return
            if opts.fail_on_calc_on_order_fills and bool(
                getattr(declaration, "calc_on_order_fills", False)
            ):
                raise UnsupportedGeneratedStrategySemantics(
                    "calc_on_order_fills generated semantics require an explicit recalc bridge"
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

    BacktestGeneratedStrategyAdapter.__name__ = (
        f"Backtest{getattr(generated_strategy_class, '__name__', 'GeneratedStrategy')}Adapter"
    )
    BacktestGeneratedStrategyAdapter.__qualname__ = BacktestGeneratedStrategyAdapter.__name__
    return BacktestGeneratedStrategyAdapter


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
        config=RuntimeConfig(),
    )


def _to_pine_bar(bar: EngineBar) -> Any:
    try:
        from pinelib.core import Bar as PineBar
    except ImportError as exc:  # pragma: no cover - depends on optional install state
        raise GeneratedStrategyBridgeError(
            "PineLib is required to convert bars for generated strategy adapters"
        ) from exc
    return PineBar(
        time=bar.time,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=0.0 if bar.volume is None else bar.volume,
    )


def _direction(value: str) -> str:
    if value not in {"long", "short"}:
        raise UnsupportedGeneratedStrategySemantics(
            f"unsupported generated strategy direction: {value!r}"
        )
    return value


__all__ = [
    "GeneratedStrategyAdapterOptions",
    "GeneratedStrategyBridgeError",
    "UnsupportedGeneratedStrategySemantics",
    "make_generated_strategy_adapter",
]
