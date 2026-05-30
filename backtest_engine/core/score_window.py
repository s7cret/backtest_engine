from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScoreWindowPlan:
    score_mode: bool
    prehistory_end_index: int
    score_start_index: int
    bar_phases: tuple[str, ...]
    effective_pre_bars: int | None


def build_score_window_plan(
    *,
    series_len: int,
    score_start_time: int | None,
    score_end_time: int | None,
    effective_pre_bars: int | None,
) -> ScoreWindowPlan:
    score_mode = score_start_time is not None or score_end_time is not None
    if not score_mode:
        return ScoreWindowPlan(
            score_mode=False,
            prehistory_end_index=-1,
            score_start_index=0,
            bar_phases=tuple("score" for _ in range(series_len)),
            effective_pre_bars=None,
        )

    if effective_pre_bars is not None and effective_pre_bars > 0:
        prehistory_end_index = min(effective_pre_bars - 1, series_len - 1)
    else:
        prehistory_end_index = 0

    return ScoreWindowPlan(
        score_mode=True,
        prehistory_end_index=prehistory_end_index,
        score_start_index=prehistory_end_index + 1,
        bar_phases=tuple(
            "prehistory" if i <= prehistory_end_index else "score"
            for i in range(series_len)
        ),
        effective_pre_bars=effective_pre_bars,
    )
