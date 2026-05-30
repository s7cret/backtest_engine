from __future__ import annotations

from dataclasses import dataclass

from backtest_engine.models import Trade
from backtest_engine.models.window import Phase, TradeResult, WarmupQuality


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


def phase_for_bar(bar_index: int | None, bar_phases: list[str]) -> Phase | None:
    if bar_index is None or bar_index < 0 or bar_index >= len(bar_phases):
        return None
    phase = bar_phases[bar_index]
    if phase == "prehistory" or phase == "score":
        return phase
    return None


def build_phase_trades(
    *,
    closed_trades: list[Trade],
    bar_phases: list[str],
) -> list[TradeResult]:
    phase_trades: list[TradeResult] = []
    for trade in closed_trades:
        entry_phase = phase_for_bar(trade.entry_bar_index, bar_phases)
        exit_phase = phase_for_bar(trade.exit_bar_index, bar_phases)
        crosses_score_boundary = (
            entry_phase == "prehistory" and exit_phase == "score"
        ) or (entry_phase == "score" and exit_phase == "prehistory")
        if entry_phase is not None:
            phase_trades.append(
                TradeResult(
                    entry_time=trade.entry_time,
                    exit_time=trade.exit_time,
                    direction=trade.direction,
                    entry_price=trade.entry_price,
                    exit_price=trade.exit_price,
                    qty=trade.qty,
                    profit=trade.profit,
                    entry_phase=entry_phase,
                    exit_phase=exit_phase,
                    crosses_score_boundary=crosses_score_boundary,
                )
            )
    return phase_trades


def classify_warmup_quality(
    *,
    bar_phases: list[str],
    effective_pre_bars: int | None,
    recommended_pre_bars_raw: int,
    requested_max_pre_bars: int,
) -> WarmupQuality | None:
    if effective_pre_bars is None:
        return None

    actual_pre_bars = bar_phases.count("prehistory") if bar_phases else 0
    insufficient_prehistory = actual_pre_bars < effective_pre_bars
    return WarmupQuality.classify(
        recommended_pre_bars_raw=recommended_pre_bars_raw,
        requested_max_pre_bars=requested_max_pre_bars,
        effective_pre_bars=effective_pre_bars,
        actual_pre_bars=actual_pre_bars,
        insufficient_prehistory=insufficient_prehistory,
    )
