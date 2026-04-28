from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

RunStatus = Literal['completed', 'failed', 'early_stopped']


@dataclass
class RunLifecycle:
    """Small timing/status helper for a single backtest run."""

    started_at: float = field(default_factory=time.perf_counter)
    status: RunStatus = 'completed'
    early_stop_reason: str | None = None

    def stop_early(self, reason: str) -> None:
        self.status = 'early_stopped'
        self.early_stop_reason = reason

    def fail(self) -> None:
        self.status = 'failed'

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000.0
