from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class MonteCarloRun:
    final_equity: float
    max_drawdown: float
    profits: tuple[float, ...]


def bootstrap_trade_profits(
    profits: Iterable[float],
    *,
    initial_capital: float,
    runs: int,
    seed: int | None = None,
) -> list[MonteCarloRun]:
    source = list(profits)
    if not source or runs <= 0:
        return list()
    rng = random.Random(seed)
    output: list[MonteCarloRun] = []
    for _ in range(runs):
        sampled = [rng.choice(source) for _ in source]
        equity = initial_capital
        peak = initial_capital
        max_dd = 0.0
        for profit in sampled:
            equity += profit
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        output.append(MonteCarloRun(equity, max_dd, tuple(sampled)))
    return output
