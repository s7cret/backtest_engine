from backtest_engine.models import Bar, Order


def build_price_path(bar: Bar) -> list[tuple[float, str]]:
    if abs(bar.open - bar.high) <= abs(bar.open - bar.low):
        return [(bar.open, "open"), (bar.high, "high"), (bar.low, "low"), (bar.close, "close")]
    return [(bar.open, "open"), (bar.low, "low"), (bar.high, "high"), (bar.close, "close")]


def limit_reached(
    order: Order, price: float, bar: Bar, mintick: float | None, assumption_ticks: int
) -> bool:
    t = (mintick or 0.0) * assumption_ticks
    if order.side == "buy":
        return price <= (order.limit_price or price) - t
    return price >= (order.limit_price or price) + t


def stop_reached(order: Order, price: float) -> bool:
    if order.side == "buy":
        return price >= (order.stop_price or price)
    return price <= (order.stop_price or price)
