from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BacktestJob:
    job_id: str
    strategy_class: type
    params: dict[str, Any] = field(default_factory=dict)
    config_overrides: dict[str, Any] = field(default_factory=dict)
    bars: object | None = None
