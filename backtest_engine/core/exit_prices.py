"""Resolve fixed exit levels at the actual opening fill, before path scanning.

Old/native callers keep absolute precedence. Pine v6 mixed TP/SL commands carry
an explicit first_trigger policy. No chart-close substitute or future bar is used.
Trailing activation/stop arbitration is deliberately a separate capability.
"""

from __future__ import annotations

import math


def _optional_number(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"exit {name} must be numeric, not bool")
    number = float(value)
    if math.isnan(number):
        return None  # Native missing numeric values; canonical intents use null/omission.
    if not math.isfinite(number):
        raise ValueError(f"exit {name} must be finite")
    return number


def resolve_exit_prices(
    *,
    direction: str,
    entry_price: float,
    mintick: float,
    limit: float | None,
    stop: float | None,
    profit: float | None,
    loss: float | None,
    policy: str = "absolute_first",
) -> tuple[float | None, float | None]:
    """Select one limit and one stop; never create duplicate competing price legs.

    A long TP triggers at the lower candidate, a long SL at the higher one;
    short positions mirror those inequalities. Zero distances are real prices.
    Both candidates can already be marketable; the scanner owns gap execution.
    """
    if direction not in ("long", "short") or policy not in ("absolute_first", "first_trigger"):
        raise ValueError("invalid exit direction or price_pair_policy")
    base, tick = _optional_number(entry_price, "entry_price"), _optional_number(mintick, "mintick")
    if base is None or tick is None or tick <= 0:
        raise ValueError("exit entry price and positive mintick are required")
    limit, stop = _optional_number(limit, "limit"), _optional_number(stop, "stop")
    sign = 1 if direction == "long" else -1

    def choose(absolute: float | None, ticks: float | None, leg: str) -> float | None:
        # Pre-v6 ignores the relative parameter when an absolute level is present.
        if policy == "absolute_first" and absolute is not None:
            return absolute
        distance = _optional_number(ticks, leg)
        if distance is None:
            return absolute
        relative = base + sign * (1 if leg == "profit" else -1) * distance * tick
        if not math.isfinite(relative):
            raise ValueError(f"exit {leg} price is outside the finite runtime range")
        if absolute is None:
            return relative
        lower_first = (direction == "long") == (leg == "profit")
        return min(absolute, relative) if lower_first else max(absolute, relative)

    return choose(limit, profit, "profit"), choose(stop, loss, "loss")


def resolve_trailing_prices(
    *, direction: str, entry_price: float, mintick: float,
    trail_price: float | None, trail_points: float | None,
    trail_offset: float | None, policy: str = "absolute_first",
) -> tuple[float, float] | None:
    """Resolve activation and offset at one real fill; no guessed missing offset."""
    absolute = _optional_number(trail_price, "trail_price")
    points = _optional_number(trail_points, "trail_points")
    offset = _optional_number(trail_offset, "trail_offset")
    if absolute is None and points is None and offset is None:
        return None
    if offset is None or (absolute is None and points is None):
        raise ValueError("trailing exit requires trail_offset and an activation level")
    if offset < 0:
        raise ValueError("trail_offset must be nonnegative")
    activation, _ = resolve_exit_prices(
        direction=direction, entry_price=entry_price, mintick=mintick,
        limit=absolute, profit=points, stop=None, loss=None, policy=policy,
    )
    distance = offset * mintick
    if not math.isfinite(distance) or (offset != 0 and distance == 0):
        raise ValueError("trail_offset is outside the finite runtime range")
    assert activation is not None
    return activation, distance
