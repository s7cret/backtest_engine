"""Price path and bar-magnifier helpers for BacktestEngine fills."""

from __future__ import annotations

from backtest_engine.broker.fill_simulator import build_price_path
from backtest_engine.errors import BarMagnifierUnavailableError, BarValidationError
from backtest_engine.models import Bar, BarSeries, Order
from backtest_engine.models.timeframe import infer_close_from_timeframe


def limit_fill_price(
    engine, order: Order, path_price: float, is_open_point: bool
) -> float:
    limit = order.limit_price if order.limit_price is not None else path_price
    if is_open_point and engine.config.limit_gap_fill_policy in (
        "tradingview",
        "open_price",
    ):
        if order.side == "sell" and path_price >= limit:
            return path_price
        if order.side == "buy" and path_price <= limit:
            return path_price
    return limit


def price_path(engine, bar: Bar) -> list[tuple[float, str]]:
    if engine.config.fill_model == "close_only":
        return [(bar.close, "close")]
    if not engine.config.use_bar_magnifier:
        return build_price_path(bar)
    if (
        not engine.config.bar_magnifier_lower_tf
        or engine.config.bar_magnifier_bars is None
    ):
        return build_price_path(bar)
    try:
        lower = engine.config.bar_magnifier_bars.get(bar.time, ())
        lower_series = (
            lower if isinstance(lower, BarSeries) else BarSeries.from_bars(lower)
        )
        validate_lower_timeframe_bars(engine, lower_series, bar)
    except Exception as exc:
        raise BarMagnifierUnavailableError(str(exc)) from exc
    if len(lower_series) == 0:
        raise BarMagnifierUnavailableError("empty lower timeframe bars")
    path: list[tuple[float, str]] = []
    for index in range(len(lower_series)):
        lower_bar = lower_series.get_bar(index)
        for price, point in build_price_path(lower_bar):
            path.append((price, f"lower[{index}].{point}"))
    return path


def validate_lower_timeframe_bars(engine, lower_series: BarSeries, parent: Bar) -> None:
    parent_close = parent.time_close
    if parent_close is None:
        parent_close = infer_parent_close(engine, parent.time)
    last_time: int | None = None
    seen: set[int] = set()
    for index in range(len(lower_series)):
        lower_bar = lower_series.get_bar(index)
        if last_time is not None and lower_bar.time < last_time:
            raise BarValidationError("lower timeframe bars are not sorted")
        if lower_bar.time in seen:
            raise BarValidationError(
                f"duplicate lower timeframe bar time {lower_bar.time}"
            )
        seen.add(lower_bar.time)
        last_time = lower_bar.time
        if lower_bar.time < parent.time or lower_bar.time >= parent_close:
            raise BarValidationError("lower timeframe bar outside parent window")
        if lower_bar.time_close is None:
            raise BarValidationError("lower timeframe bar missing time_close")
        if lower_bar.time_close <= lower_bar.time:
            raise BarValidationError("lower timeframe bar has invalid/open time_close")
        if lower_bar.time_close > parent_close:
            raise BarValidationError("lower timeframe bar closes outside parent window")
        if lower_bar.high < max(lower_bar.open, lower_bar.close, lower_bar.low):
            raise BarValidationError("lower timeframe bar has invalid OHLC high")
        if lower_bar.low > min(lower_bar.open, lower_bar.close, lower_bar.high):
            raise BarValidationError("lower timeframe bar has invalid OHLC low")


def validate_supplied_bar_magnifier_bars(engine, series: BarSeries) -> None:
    for index in range(len(series)):
        parent = series.get_bar(index)
        lower = engine.config.bar_magnifier_bars.get(parent.time, ())
        if not lower:
            continue
        lower_series = (
            lower if isinstance(lower, BarSeries) else BarSeries.from_bars(lower)
        )
        try:
            validate_lower_timeframe_bars(engine, lower_series, parent)
        except Exception as exc:
            raise BarMagnifierUnavailableError(str(exc)) from exc


def infer_parent_close(engine, parent_open: int) -> int:
    return infer_close_from_timeframe(parent_open, engine.config.timeframe)
