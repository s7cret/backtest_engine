import pytest

from backtest_engine.models import ExecutionWindow


def test_execution_window_contract_validates_fetch_contains_score_window():
    window = ExecutionWindow(
        requested_start_ms=1_000,
        requested_end_ms=2_000,
        score_start_ms=1_000,
        score_end_ms=2_000,
        provider_fetch_start_ms=0,
        provider_fetch_end_ms=2_000,
        pre_bars_count=1,
        score_bars_count=1,
        data_source_kind="MARKETDATA_API_PRODUCT",
    )

    assert window.provider_fetch_start_ms <= window.score_start_ms
    assert window.provider_fetch_end_ms >= window.score_end_ms


def test_execution_window_rejects_fetch_after_score_start():
    with pytest.raises(ValueError, match="provider_fetch_start_ms"):
        ExecutionWindow(
            requested_start_ms=1_000,
            requested_end_ms=2_000,
            score_start_ms=1_000,
            score_end_ms=2_000,
            provider_fetch_start_ms=1_001,
            provider_fetch_end_ms=2_000,
            pre_bars_count=0,
            score_bars_count=1,
        )
