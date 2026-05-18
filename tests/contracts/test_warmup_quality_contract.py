from backtest_engine.models import PrehistoryPlan, WarmupQuality


def test_prehistory_plan_keeps_planner_output_separate_from_outcome():
    plan = PrehistoryPlan(
        recommended_pre_bars_raw=1_000,
        requested_max_pre_bars=500,
        effective_pre_bars=500,
        min_pre_bars=0,
        max_pre_bars=500,
        reasons=["recursive=1000"],
        confidence="heuristic",
    )

    assert plan.effective_pre_bars == 500
    assert plan.confidence == "heuristic"


def test_warmup_quality_classifies_complete_capped_and_partial():
    complete = WarmupQuality.classify(
        recommended_pre_bars_raw=250,
        requested_max_pre_bars=500,
        effective_pre_bars=250,
        actual_pre_bars=250,
    )
    capped = WarmupQuality.classify(
        recommended_pre_bars_raw=1_000,
        requested_max_pre_bars=500,
        effective_pre_bars=500,
        actual_pre_bars=500,
    )
    partial = WarmupQuality.classify(
        recommended_pre_bars_raw=1_000,
        requested_max_pre_bars=1_000,
        effective_pre_bars=1_000,
        actual_pre_bars=700,
    )

    assert complete.warmup_confidence == "complete"
    assert capped.warmup_confidence == "capped"
    assert capped.insufficient_prehistory is False
    assert partial.warmup_confidence == "partial"
