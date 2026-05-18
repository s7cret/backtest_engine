from datetime import datetime, timezone

from backtest_engine import BacktestConfig


def test_backtest_config_score_window_defaults_preserve_existing_behavior():
    config = BacktestConfig(symbol="BTCUSDT", timeframe="15m", start_time=1, end_time=2)

    assert config.score_start_time is None
    assert config.score_end_time is None
    assert config.auto_pre_bars is False
    assert config.min_pre_bars == 0
    assert config.max_pre_bars == 0
    assert config.warmup_confidence_mode == "unknown"
    assert config.data_source_kind == "BARS"


def test_backtest_config_accepts_score_window_schema_fields():
    start = datetime(2026, 5, 10, tzinfo=timezone.utc)
    end = datetime(2026, 5, 14, tzinfo=timezone.utc)

    config = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="15m",
        start_time=1,
        end_time=2,
        score_start_time=start,
        score_end_time=end,
        auto_pre_bars=True,
        min_pre_bars=0,
        max_pre_bars=1000,
        warmup_confidence_mode="accurate",
        data_source_kind="MARKETDATA_API_PRODUCT",
    )

    assert config.score_start_time == start
    assert config.score_end_time == end
    assert config.auto_pre_bars is True
    assert config.max_pre_bars == 1000
    assert config.warmup_confidence_mode == "accurate"
    assert config.data_source_kind == "MARKETDATA_API_PRODUCT"


def test_backtest_config_snapshot_includes_score_window_schema_fields():
    config = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="15m",
        start_time=1,
        end_time=2,
        auto_pre_bars=True,
        max_pre_bars=500,
        warmup_confidence_mode="balanced",
        data_source_kind="CSV_EXACT_ORACLE",
    )

    snapshot = config.snapshot()

    assert snapshot["score_start_time"] is None
    assert snapshot["score_end_time"] is None
    assert snapshot["auto_pre_bars"] is True
    assert snapshot["min_pre_bars"] == 0
    assert snapshot["max_pre_bars"] == 500
    assert snapshot["warmup_confidence_mode"] == "balanced"
    assert snapshot["data_source_kind"] == "CSV_EXACT_ORACLE"
