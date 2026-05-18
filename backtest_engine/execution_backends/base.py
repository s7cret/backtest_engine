from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


@dataclass(slots=True)
class BackendBarResult:
    time: int
    phase: str
    equity: float | None = None
    netprofit: float | None = None
    openprofit: float | None = None
    position_size: float | None = None
    closedtrades: int | None = None
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class BackendExecutionResult:
    bar_results: list[BackendBarResult]
    trades: list[Any] = field(default_factory=list)
    plots: Any | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    raw_context: Any | None = None
    raw_result: Any | None = None


class ExecutionBackend(Protocol):
    name: str

    def execute(
        self,
        strategy_class: type | Any,
        bars: Sequence[Any],
        *,
        config: Any,
        execution_window: Any,
        effective_pre_bars: int = 0,
        runtime_kwargs: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> BackendExecutionResult:
        ...
