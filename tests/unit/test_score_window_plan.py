from backtest_engine.core.score_window import build_score_window_plan


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
