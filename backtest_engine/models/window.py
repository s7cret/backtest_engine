from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Phase = Literal["prehistory", "score"]
WarmupConfidence = Literal["complete", "capped", "partial", "unknown"]
PlanConfidence = Literal["exact", "heuristic", "unknown"]


@dataclass(frozen=True, slots=True)
class ExecutionWindow:
    requested_start_ms: int
    requested_end_ms: int
    score_start_ms: int
    score_end_ms: int
    provider_fetch_start_ms: int
    provider_fetch_end_ms: int
    pre_bars_count: int
    score_bars_count: int
    data_source_kind: str = "BARS"

    def __post_init__(self) -> None:
        if self.provider_fetch_start_ms > self.score_start_ms:
            raise ValueError("provider_fetch_start_ms must be <= score_start_ms")
        if self.provider_fetch_end_ms < self.score_end_ms:
            raise ValueError("provider_fetch_end_ms must be >= score_end_ms")
        if self.score_start_ms > self.score_end_ms:
            raise ValueError("score_start_ms must be <= score_end_ms")
        if self.requested_start_ms > self.requested_end_ms:
            raise ValueError("requested_start_ms must be <= requested_end_ms")
        if self.pre_bars_count < 0 or self.score_bars_count < 0:
            raise ValueError("bar counts must be non-negative")


@dataclass(frozen=True, slots=True)
class PrehistoryPlan:
    recommended_pre_bars_raw: int
    requested_max_pre_bars: int
    effective_pre_bars: int
    min_pre_bars: int = 0
    max_pre_bars: int = 0
    reasons: list[str] = field(default_factory=list)
    confidence: PlanConfidence = "unknown"

    def __post_init__(self) -> None:
        for name in (
            "recommended_pre_bars_raw",
            "requested_max_pre_bars",
            "effective_pre_bars",
            "min_pre_bars",
            "max_pre_bars",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True, slots=True)
class WarmupQuality:
    recommended_pre_bars_raw: int
    requested_max_pre_bars: int
    effective_pre_bars: int
    actual_pre_bars: int
    capped_by_max_pre_bars: bool
    insufficient_prehistory: bool
    warmup_confidence: WarmupConfidence
    provider_fetch_start_ms: int | None = None
    requested_start_ms: int | None = None
    first_output_time_ms: int | None = None
    score_rows_written: int | None = None
    reasons: list[str] = field(default_factory=list)

    @classmethod
    def classify(
        cls,
        *,
        recommended_pre_bars_raw: int,
        requested_max_pre_bars: int,
        effective_pre_bars: int,
        actual_pre_bars: int,
        insufficient_prehistory: bool = False,
        **kwargs: object,
    ) -> "WarmupQuality":
        capped = recommended_pre_bars_raw > requested_max_pre_bars
        if actual_pre_bars < effective_pre_bars or insufficient_prehistory:
            confidence: WarmupConfidence = "partial"
        elif capped:
            confidence = "capped"
        elif effective_pre_bars >= recommended_pre_bars_raw:
            confidence = "complete"
        else:
            confidence = "unknown"
        return cls(
            recommended_pre_bars_raw=recommended_pre_bars_raw,
            requested_max_pre_bars=requested_max_pre_bars,
            effective_pre_bars=effective_pre_bars,
            actual_pre_bars=actual_pre_bars,
            capped_by_max_pre_bars=capped,
            insufficient_prehistory=insufficient_prehistory,
            warmup_confidence=confidence,
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class TradeResult:
    entry_time: int
    exit_time: int | None
    direction: str
    entry_price: float
    exit_price: float | None
    qty: float
    profit: float | None
    entry_phase: Phase
    exit_phase: Phase | None
    crosses_score_boundary: bool
