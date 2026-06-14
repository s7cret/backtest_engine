"""Bridge-owned Pine scalar and strategy-context helpers.

This module keeps PineLib-specific bridge state out of the generated strategy
adapter façade. It intentionally remains private: the public entrypoint is
:func:`backtest_engine.adapters.generated_strategy.make_generated_strategy_adapter`.
"""

from __future__ import annotations

from typing import Any

from backtest_engine.context import StrategyContext as EngineStrategyContext
from backtest_engine.adapters.generated_strategy_errors import (
    GeneratedStrategyBridgeError,
    UnsupportedGeneratedStrategySemantics,
)


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
        if _is_pine_na(self._current):
            return self._current
        return self._current + _unwrap_scalar(other)

    def __radd__(self, other: Any) -> Any:
        if _is_pine_na(self._current):
            return self._current
        return _unwrap_scalar(other) + self._current

    def __sub__(self, other: Any) -> Any:
        if _is_pine_na(self._current):
            return self._current
        return self._current - _unwrap_scalar(other)

    def __rsub__(self, other: Any) -> Any:
        if _is_pine_na(self._current):
            return self._current
        return _unwrap_scalar(other) - self._current

    def __mul__(self, other: Any) -> Any:
        if _is_pine_na(self._current):
            return self._current
        return self._current * _unwrap_scalar(other)

    def __rmul__(self, other: Any) -> Any:
        if _is_pine_na(self._current):
            return self._current
        return _unwrap_scalar(other) * self._current

    def __truediv__(self, other: Any) -> Any:
        if _is_pine_na(self._current):
            return self._current
        return self._current / _unwrap_scalar(other)

    def __rtruediv__(self, other: Any) -> Any:
        if _is_pine_na(self._current):
            return self._current
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


def _is_pine_na(value: Any) -> bool:
    try:
        from pinelib.core.na import is_na
    except ImportError:
        return False
    return bool(is_na(value))


def _none_if_pine_na(value: Any) -> Any:
    return None if _is_pine_na(value) else value


def _pine_na() -> Any:
    try:
        from pinelib.core import na
    except ImportError as exc:  # pragma: no cover - depends on optional install state
        raise GeneratedStrategyBridgeError(
            "PineLib is required for Pine na values"
        ) from exc
    return na


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
            _pine_na()
            if state.position_avg_price is None
            else float(state.position_avg_price)
        )
        self.opentrades.set_current(int(state.open_trades))
        self.closedtrades.set_current(int(state.closed_trades))
        self.max_drawdown.set_current(float(state.max_drawdown))
        self.max_runup.set_current(float(state.max_runup))
        self.wintrades.set_current(int(state.win_trades))
        self.losstrades.set_current(int(state.loss_trades))
        self.eventrades.set_current(int(state.even_trades))

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

    def risk_max_intraday_filled_orders(
        self, value: float, type: str = "fixed"
    ) -> None:
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
            id=id,
            direction=_direction(direction),
            qty=_none_if_pine_na(qty),
            limit=_none_if_pine_na(limit),
            stop=_none_if_pine_na(stop),
            comment=comment,
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
        comment: str | None = None,
        *,
        source_map: object | None = None,
    ) -> None:
        del source_map
        self._engine_ctx.order(
            id=id,
            direction=_direction(direction),
            qty=_none_if_pine_na(qty),
            limit=_none_if_pine_na(limit),
            stop=_none_if_pine_na(stop),
            oca_name=oca_name,
            oca_type=oca_type,
            comment=comment,
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
        comment: str | None = None,
        *,
        source_map: object | None = None,
    ) -> None:
        del source_map
        self._engine_ctx.exit(
            id=id,
            from_entry=from_entry,
            qty=_none_if_pine_na(qty),
            qty_percent=_none_if_pine_na(qty_percent),
            limit=_none_if_pine_na(limit),
            stop=_none_if_pine_na(stop),
            profit=_none_if_pine_na(profit),
            loss=_none_if_pine_na(loss),
            trail_price=_none_if_pine_na(trail_price),
            trail_points=_none_if_pine_na(trail_points),
            trail_offset=_none_if_pine_na(trail_offset),
            oca_name=oca_name,
            oca_type=oca_type,
            comment=comment,
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
        self._engine_ctx.close(
            id=id,
            qty=_none_if_pine_na(qty),
            qty_percent=_none_if_pine_na(qty_percent),
            immediately=immediately,
            comment=comment,
        )

    def close_all(
        self,
        immediately: bool = False,
        *,
        comment: str | None = None,
        source_map: object | None = None,
    ) -> None:
        del source_map
        self._engine_ctx.close_all(immediately=immediately, comment=comment)

    def cancel(self, id: str, *, source_map: object | None = None) -> None:
        del source_map
        self._engine_ctx.cancel(id)

    def cancel_all(self, *, source_map: object | None = None) -> None:
        del source_map
        self._engine_ctx.cancel_all()

    def accept_orders_from_generated_code(self) -> None:
        return None


def _direction(value: str) -> str:
    if value not in {"long", "short"}:
        raise UnsupportedGeneratedStrategySemantics(
            f"unsupported generated strategy direction: {value!r}"
        )
    return value
