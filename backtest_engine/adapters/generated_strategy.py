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


class _BridgeScalarSeries:
    """Mutable scalar with Pine history semantics for strategy-owned fields."""

    def __init__(self, value: Any = 0.0) -> None:
        self._current: Any = value
        self._history: list[Any] = []

    @property
    def current(self) -> Any:
        return self._current

    @property
    def committed_length(self) -> int:
        return len(self._history)

    def set_current(self, value: Any) -> None:
        self._current = value

    def commit_current(self) -> None:
        self._history.append(self._current)

    def __getitem__(self, offset: int) -> Any:
        if offset < 0:
            raise IndexError("negative history offsets are not supported")
        if offset == 0:
            return self._current
        if offset <= len(self._history):
            return self._history[-offset]
        return 0

    def __float__(self) -> float:
        return float(self._current)

    def __int__(self) -> int:
        return int(self._current)

    def __bool__(self) -> bool:
        return bool(self._current)

    def __add__(self, other: Any) -> Any:
        return self._current + _unwrap_scalar(other)

    def __radd__(self, other: Any) -> Any:
        return _unwrap_scalar(other) + self._current

    def __sub__(self, other: Any) -> Any:
        return self._current - _unwrap_scalar(other)

    def __rsub__(self, other: Any) -> Any:
        return _unwrap_scalar(other) - self._current

    def __mul__(self, other: Any) -> Any:
        return self._current * _unwrap_scalar(other)

    def __rmul__(self, other: Any) -> Any:
        return _unwrap_scalar(other) * self._current

    def __truediv__(self, other: Any) -> Any:
        return self._current / _unwrap_scalar(other)

    def __rtruediv__(self, other: Any) -> Any:
        return _unwrap_scalar(other) / self._current

    def __eq__(self, other: object) -> bool:
        return self._current == _unwrap_scalar(other)

    def __lt__(self, other: Any) -> bool:
        return self._current < _unwrap_scalar(other)

    def __le__(self, other: Any) -> bool:
        return self._current <= _unwrap_scalar(other)

    def __gt__(self, other: Any) -> bool:
        return self._current > _unwrap_scalar(other)

    def __ge__(self, other: Any) -> bool:
        return self._current >= _unwrap_scalar(other)


def _unwrap_scalar(value: Any) -> Any:
    return getattr(value, "_current", value)


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
    fail_on_config_mismatch: bool = True


class _BridgeStrategyContext:
    def __init__(self, engine_ctx: EngineStrategyContext) -> None:
        self._engine_ctx = engine_ctx
        self._runtime: Any | None = None
        self.initial_capital = float(getattr(engine_ctx.config, "initial_capital", 0.0))
        self.equity = _BridgeScalarSeries()
        self.netprofit = _BridgeScalarSeries()
        self.openprofit = _BridgeScalarSeries()
        self.grossprofit = _BridgeScalarSeries()
        self.grossloss = _BridgeScalarSeries()
        self.position_size = _BridgeScalarSeries()
        self.position_avg_price = _BridgeScalarSeries()
        self.opentrades = _BridgeScalarSeries(0)
        self.closedtrades = _BridgeScalarSeries(0)
        self.max_drawdown = _BridgeScalarSeries()
        self.max_runup = _BridgeScalarSeries()
        self.wintrades = _BridgeScalarSeries(0)
        self.losstrades = _BridgeScalarSeries(0)
        self.eventrades = _BridgeScalarSeries(0)
        self._sync_from_engine()

    def attach_runtime(self, runtime: Any) -> None:
        self._runtime = runtime
        runtime.strategy = self

    def _sync_from_engine(self) -> None:
        state = self._engine_ctx.state
        self.equity.set_current(float(state.equity))
        self.netprofit.set_current(float(state.net_profit))
        self.openprofit.set_current(float(state.open_profit))
        self.grossprofit.set_current(float(state.gross_profit))
        self.grossloss.set_current(float(state.gross_loss))
        self.position_size.set_current(float(state.position_size))
        self.position_avg_price.set_current(
            _pine_na() if state.position_avg_price is None else float(state.position_avg_price)
        )
        self.opentrades.set_current(int(state.open_trades))
        self.closedtrades.set_current(int(state.closed_trades))
        self.max_drawdown.set_current(float(state.max_drawdown))
        self.max_runup.set_current(float(state.max_runup))
        closed = getattr(state, "_closed_trades_ref", [])
        self.wintrades.set_current(sum(1 for trade in closed if trade.profit > 0))
        self.losstrades.set_current(sum(1 for trade in closed if trade.profit < 0))
        self.eventrades.set_current(sum(1 for trade in closed if trade.profit == 0))

    def _commit_scalar_history(self) -> None:
        for value in (
            self.equity,
            self.netprofit,
            self.openprofit,
            self.grossprofit,
            self.grossloss,
            self.position_size,
            self.position_avg_price,
            self.opentrades,
            self.closedtrades,
            self.max_drawdown,
            self.max_runup,
            self.wintrades,
            self.losstrades,
            self.eventrades,
        ):
            value.commit_current()

    def closedtrades_max_runup(self, index: int | float) -> float:
        return self._engine_ctx.state.closedtrades_max_runup(int(index))

    def closedtrades_max_drawdown(self, index: int | float) -> float:
        return self._engine_ctx.state.closedtrades_max_drawdown(int(index))

    def closedtrades_entry_id(self, index: int | float) -> str:
        return self._engine_ctx.state.closedtrades_entry_id(int(index))

    def closedtrades_exit_id(self, index: int | float) -> str | None:
        return self._engine_ctx.state.closedtrades_exit_id(int(index))

    def closedtrades_entry_price(self, index: int | float) -> float:
        return self._engine_ctx.state.closedtrades_entry_price(int(index))

    def closedtrades_exit_price(self, index: int | float) -> float | None:
        return self._engine_ctx.state.closedtrades_exit_price(int(index))

    def closedtrades_entry_time(self, index: int | float) -> int:
        return self._engine_ctx.state.closedtrades_entry_time(int(index))

    def closedtrades_exit_time(self, index: int | float) -> int | None:
        return self._engine_ctx.state.closedtrades_exit_time(int(index))

    def closedtrades_commission(self, index: int | float) -> float:
        return self._engine_ctx.state.closedtrades_commission(int(index))

    def closedtrades_size(self, index: int | float) -> float:
        return self._engine_ctx.state.closedtrades_size(int(index))

    def closedtrades_qty(self, index: int | float) -> float:
        return self._engine_ctx.state.closedtrades_qty(int(index))

    def closedtrades_side(self, index: int | float) -> str:
        return self._engine_ctx.state.closedtrades_side(int(index))

    def closedtrades_profit(self, index: int | float) -> float:
        return self._engine_ctx.state.closedtrades_profit(int(index))

    def closedtrades_profit_percent(self, index: int | float) -> float:
        return self._engine_ctx.state.closedtrades_profit_percent(int(index))

    def closedtrades_entry_bar_index(self, index: int | float) -> int:
        return self._engine_ctx.state.closedtrades_entry_bar_index(int(index))

    def closedtrades_exit_bar_index(self, index: int | float) -> int | None:
        return self._engine_ctx.state.closedtrades_exit_bar_index(int(index))

    def opentrades_max_runup(self, index: int | float) -> float:
        return self._engine_ctx.state.opentrades_max_runup(int(index))

    def opentrades_max_drawdown(self, index: int | float) -> float:
        return self._engine_ctx.state.opentrades_max_drawdown(int(index))

    def opentrades_entry_id(self, index: int | float) -> str:
        return self._engine_ctx.state.opentrades_entry_id(int(index))

    def opentrades_entry_price(self, index: int | float) -> float:
        return self._engine_ctx.state.opentrades_entry_price(int(index))

    def opentrades_entry_time(self, index: int | float) -> int:
        return self._engine_ctx.state.opentrades_entry_time(int(index))

    def opentrades_entry_bar_index(self, index: int | float) -> int:
        return self._engine_ctx.state.opentrades_entry_bar_index(int(index))

    def opentrades_commission(self, index: int | float) -> float:
        return self._engine_ctx.state.opentrades_commission(int(index))

    def opentrades_size(self, index: int | float) -> float:
        return self._engine_ctx.state.opentrades_size(int(index))

    def opentrades_qty(self, index: int | float) -> float:
        return self._engine_ctx.state.opentrades_qty(int(index))

    def opentrades_side(self, index: int | float) -> str:
        return self._engine_ctx.state.opentrades_side(int(index))

    def opentrades_profit(self, index: int | float) -> float:
        return self._engine_ctx.state.opentrades_profit(int(index))

    def opentrades_profit_percent(self, index: int | float) -> float:
        return self._engine_ctx.state.opentrades_profit_percent(int(index))

    def risk_allow_entry_in(self, direction: str) -> None:
        self._engine_ctx.risk_allow_entry_in(direction)

    def risk_max_drawdown(self, value: float, type: str) -> None:
        self._engine_ctx.risk_max_drawdown(value, type)

    def risk_max_position_size(self, value: float, type: str = "fixed") -> None:
        self._engine_ctx.risk_max_position_size(value, type)

    def risk_max_intraday_loss(self, value: float, type: str) -> None:
        self._engine_ctx.risk_max_intraday_loss(value, type)

    def risk_max_intraday_filled_orders(self, value: float, type: str = "fixed") -> None:
        self._engine_ctx.risk_max_intraday_filled_orders(value, type)

    def entry(
        self,
        id: str,
        direction: str,
        qty: float | None = None,
        limit: float | None = None,
        stop: float | None = None,
        comment: str | None = None,
        *,
        source_map: object | None = None,
    ) -> None:
        del source_map
        self._engine_ctx.entry(
            id=id, direction=_direction(direction), qty=qty, limit=limit, stop=stop, comment=comment
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
        oca_name: str | None = None,
        oca_type: str | None = None,
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
            oca_name=oca_name,
            oca_type=oca_type,
        )

    def close(
        self,
        id: str,
        qty: float | None = None,
        qty_percent: float | None = None,
        immediately: bool = False,
        comment: str | None = None,
        *,
        source_map: object | None = None,
    ) -> None:
        del source_map
        self._engine_ctx.close(id=id, qty=qty, qty_percent=qty_percent, immediately=immediately, comment=comment)

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
                getattr(self.generated, "ctx", None), adapter_options, ctx.config
            )
            self._bridge_ctx = _BridgeStrategyContext(ctx)
            self._bridge_ctx.attach_runtime(self._pine_runtime)
            self.generated.ctx = self._bridge_ctx
            self._active_engine_bar_index: int | None = None

        def _process_bar(self, bar: EngineBar, bar_index: int) -> None:
            pine_bar = _to_pine_bar(bar)
            if self._active_engine_bar_index != bar_index:
                if self._active_engine_bar_index is not None:
                    self._pine_runtime.end_bar()
                self._pine_runtime.begin_bar(pine_bar)
                self._active_engine_bar_index = bar_index
            self._bridge_ctx._sync_from_engine()
            process = getattr(self.generated, "_process_bar", None)
            if not callable(process):
                raise GeneratedStrategyBridgeError("generated strategy must expose _process_bar(bar)")
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
            generated_ctx: Any, opts: GeneratedStrategyAdapterOptions, engine_config: Any
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

    BacktestGeneratedStrategyAdapter.__name__ = (
        f"Backtest{getattr(generated_strategy_class, '__name__', 'GeneratedStrategy')}Adapter"
    )
    BacktestGeneratedStrategyAdapter.__qualname__ = BacktestGeneratedStrategyAdapter.__name__
    return BacktestGeneratedStrategyAdapter


def _declaration_config_diff(declaration: Any, engine_config: Any) -> dict[str, dict[str, Any]]:
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
        config=RuntimeConfig(),
    )


def _pine_na() -> Any:
    try:
        from pinelib.core import na
    except ImportError as exc:  # pragma: no cover - depends on optional install state
        raise GeneratedStrategyBridgeError("PineLib is required for Pine na values") from exc
    return na


def _pine_timestamp(value: int | None) -> int | None:
    if value is None:
        return None
    # BacktestEngine bar feeds may use Unix seconds, while Pine runtime/Pine
    # builtins expose `time`/`time_close` in milliseconds. Convert seconds at
    # the bridge boundary so session/timezone functions evaluate on real dates.
    return int(value) * 1000 if abs(int(value)) < 10_000_000_000 else int(value)


def _to_pine_bar(bar: EngineBar) -> Any:
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
        time_close=_pine_timestamp(getattr(bar, "time_close", None)),
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
