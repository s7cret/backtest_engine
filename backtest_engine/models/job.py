from dataclasses import dataclass, field
from typing import Any

from .bar import Bar
from .bar_series import BarSeries


@dataclass(frozen=True)
class BacktestJob:
    job_id: str
    strategy_class: type
    params: dict[str, Any] = field(default_factory=dict)
    config_overrides: dict[str, Any] = field(default_factory=dict)
    bars: BarSeries | list[Bar] | None = None
