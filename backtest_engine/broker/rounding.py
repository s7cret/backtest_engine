from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING


def round_to_step(value: float, step: float | None, mode: str = "nearest") -> float:
    if not step:
        return value
    # Use Decimal to avoid binary float bias at half-step boundaries.
    # str(float) can carry float noise; round via Decimal with a tiny epsilon
    # so that values like 26.694999999999997 (true 26.695) round half-up.
    eps = Decimal(str(step)) / Decimal("1E12")
    d = Decimal(str(value)) + eps
    s = Decimal(str(step))
    if mode == "floor":
        return float(d.quantize(s, rounding=ROUND_FLOOR))
    if mode == "ceil":
        return float(d.quantize(s, rounding=ROUND_CEILING))
    # nearest = half-up, matching TradingView cent-rounding on split-adjusted prices
    return float(d.quantize(s, rounding=ROUND_HALF_UP))
