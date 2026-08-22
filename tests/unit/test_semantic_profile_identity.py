from openpine_contracts import SemanticProfile

from backtest_engine import BacktestConfig
from backtest_engine.errors import ConfigError


def test_semantic_profile_is_in_run_identity() -> None:
    legacy = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="15m",
        start_time=1,
        end_time=2,
        semantic_profile=SemanticProfile.LEGACY_4X,
    finality_policy="ALLOW_OPEN",
     )
    strict = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="15m",
        start_time=1,
        end_time=2,
        semantic_profile=SemanticProfile.STRICT_5X,
    finality_policy="ALLOW_OPEN",
     )
    assert legacy.snapshot()["semantic_profile"] == "legacy_4x"
    assert strict.snapshot()["semantic_profile"] == "strict_5x"
    assert legacy.snapshot() != strict.snapshot()


def test_unknown_semantic_profile_is_config_error() -> None:
    try:
        BacktestConfig(
            symbol="BTCUSDT",
            timeframe="15m",
            start_time=1,
            end_time=2,
            semantic_profile="nope",
        finality_policy="ALLOW_OPEN",
         )
    except ConfigError as exc:
        assert "semantic_profile" in str(exc)
    else:
        raise AssertionError("unknown semantic profile must fail")
