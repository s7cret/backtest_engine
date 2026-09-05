from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING, ROUND_DOWN
import math


def round_to_step(value: float, step: float | None, mode: str = "nearest") -> float:
    """Round to an actual multiple of step, not its decimal-place count."""
    modes = {
        "nearest": ROUND_HALF_UP,
        "floor": ROUND_FLOOR,
        "ceil": ROUND_CEILING,
        "truncate": ROUND_DOWN,
    }
    if mode not in {*modes, "none"}:
        raise ValueError(f"unknown rounding mode: {mode}")
    if not math.isfinite(value):
        raise ValueError("rounding value must be finite")
    if step is not None and (not math.isfinite(step) or step <= 0):
        raise ValueError("rounding step must be positive and finite")
    if step is None or mode == "none":
        return value
    d, s = Decimal(str(value)), Decimal(str(step))
    # Preserve the existing float-boundary tolerance, expressed in step units.
    eps = s / Decimal("1E9")
    if mode == "floor":
        d += eps
    elif mode == "ceil":
        d -= eps
    elif mode == "nearest":
        d += eps.copy_sign(d)
    else:
        d += eps.copy_sign(d)
    return float((d / s).to_integral_value(rounding=modes[mode]) * s)
