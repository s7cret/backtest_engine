from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING


def round_to_step(value: float, step: float | None, mode: str = "nearest") -> float:
    if not step:
        return value
    # Use Decimal to avoid binary float bias at half-step boundaries.
    # str(float) can carry float noise; round via Decimal with a tiny epsilon
    # so that values like 26.694999999999997 (true 26.695) round half-up.
    # The epsilon must also absorb one-ulp products such as
    # 8267.3 * 0.30 = 2480.1899999999996, which TradingView treats as
    # exactly 2480.190 when flooring to qty_step=0.001.
    eps = Decimal(str(step)) / Decimal("1E9")
    d = Decimal(str(value))
    s = Decimal(str(step)).normalize()
    if mode == "floor":
        return float((d + eps).quantize(s, rounding=ROUND_FLOOR))
    if mode == "ceil":
        return float((d - eps).quantize(s, rounding=ROUND_CEILING))
    # nearest = half-up, matching TradingView cent-rounding on split-adjusted prices
    return float((d + eps).quantize(s, rounding=ROUND_HALF_UP))
