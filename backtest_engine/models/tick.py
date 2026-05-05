from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Tick:
    """Realtime/tick input point for future calc_on_every_tick replay.

    This model is intentionally small and data-only. BacktestEngine still fails
    closed for calc_on_every_tick until Pine realtime rollback/commit semantics
    and a tick scheduler are implemented and oracle-verified.
    """

    time: int
    price: float
    volume: float | None = None
    bid: float | None = None
    ask: float | None = None
