from backtest_engine.core.score_window import (
    build_phase_trades,
    build_score_window_plan,
    phase_for_bar,
)
from backtest_engine.models import Trade


def test_score_window_plan_disabled_marks_all_bars_as_score() -> None:
    plan = build_score_window_plan(
        series_len=3,
        score_start_time=None,
        score_end_time=None,
        effective_pre_bars=5,
    )

    assert plan.score_mode is False
    assert plan.prehistory_end_index == -1
    assert plan.score_start_index == 0
    assert plan.bar_phases == ("score", "score", "score")
    assert plan.effective_pre_bars is None


def test_score_window_plan_uses_effective_pre_bars_for_phase_boundary() -> None:
    plan = build_score_window_plan(
        series_len=5,
        score_start_time=1_000,
        score_end_time=2_000,
        effective_pre_bars=2,
    )

    assert plan.score_mode is True
    assert plan.prehistory_end_index == 1
    assert plan.score_start_index == 2
    assert plan.bar_phases == ("prehistory", "prehistory", "score", "score", "score")
    assert plan.effective_pre_bars == 2


def test_score_window_plan_preserves_default_first_bar_boundary() -> None:
    plan = build_score_window_plan(
        series_len=4,
        score_start_time=1_000,
        score_end_time=None,
        effective_pre_bars=None,
    )

    assert plan.prehistory_end_index == 0
    assert plan.score_start_index == 1
    assert plan.bar_phases == ("prehistory", "score", "score", "score")
    assert plan.effective_pre_bars is None


def test_phase_for_bar_returns_none_for_invalid_or_unknown_phase() -> None:
    assert phase_for_bar(None, ["score"]) is None
    assert phase_for_bar(-1, ["score"]) is None
    assert phase_for_bar(2, ["score"]) is None
    assert phase_for_bar(0, ["unknown"]) is None


def test_build_phase_trades_marks_score_boundary_crossings() -> None:
    trade = Trade(
        id="t1",
        entry_id="entry",
        exit_id="exit",
        direction="long",
        entry_time=10,
        entry_bar_index=0,
        entry_price=100.0,
        exit_time=20,
        exit_bar_index=2,
        exit_price=110.0,
        qty=1.0,
        commission_entry=0.0,
        commission_exit=0.0,
        profit=10.0,
        profit_percent=10.0,
    )

    phase_trades = build_phase_trades(
        closed_trades=[trade],
        bar_phases=["prehistory", "prehistory", "score"],
    )

    assert len(phase_trades) == 1
    assert phase_trades[0].entry_phase == "prehistory"
    assert phase_trades[0].exit_phase == "score"
    assert phase_trades[0].crosses_score_boundary is True
