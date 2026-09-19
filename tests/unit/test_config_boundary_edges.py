"""Portable configuration and numeric admission must fail closed, not repair input."""
import builtins
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from backtest_engine.broker.rounding import round_to_step
from backtest_engine.config import BacktestConfig
from backtest_engine.config_transport import EffectiveStrategyConfig, execution_config_values
from backtest_engine.core.engine_validation import validate_backtest_config
from backtest_engine.core.execution_event import describe_execution
from backtest_engine.core.order_metadata import execution_metadata
from backtest_engine.errors import BarValidationError, ConfigError, StrategyRuntimeError
from backtest_engine.models.bar import Bar
from backtest_engine.models.instrument import InstrumentModel
from backtest_engine.models.timeframe import infer_close_from_timeframe


BASE = {"symbol": "TEST", "timeframe": "1", "start_time": 0, "end_time": 120000}


@pytest.mark.parametrize(
    "nested, message",
    [
        ({"value": float("nan")}, "must be finite"),
        ({"value": float("inf")}, "must be finite"),
        ({1: "value"}, "keys must be strings"),
        ({"value": object()}, "nonportable"),
    ],
)
def test_nested_configuration_is_not_silently_serialized(nested, message):
    with pytest.raises(ConfigError, match=message):
        EffectiveStrategyConfig.resolve({**BASE, "warmup_metadata": nested})


@pytest.mark.parametrize("field", ["runtime", "output_dir", "realtime_ticks"])
def test_local_objects_require_separate_worker_admission(field):
    with pytest.raises(ConfigError, match="dedicated worker admission"):
        execution_config_values(SimpleNamespace(**{field: object()}))
    with pytest.raises(ConfigError, match="dedicated worker admission"):
        EffectiveStrategyConfig.resolve({**BASE, field: object()})


def test_conflicting_quantity_aliases_are_rejected_in_both_boundaries():
    conflicting = {"qty_rounding": "floor", "qty_rounding_mode": "ceil"}
    with pytest.raises(ConfigError, match="conflicting"):
        execution_config_values(SimpleNamespace(**conflicting))
    with pytest.raises(ConfigError, match="conflicting"):
        EffectiveStrategyConfig.resolve({**BASE, **conflicting})


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("start_time", True, "must be an integer"),
        ("start_time", "0", "must be an integer"),
        ("symbol", 7, "must be a string"),
        ("required_outputs", "closed_trades", "collection of strings"),
        ("required_metrics", [7], "collection of strings"),
        ("instrument_model", {"unknown_field": 1}, "invalid instrument_model"),
    ],
)
def test_portable_scalar_and_collection_types_are_exact(field, value, message):
    with pytest.raises(ConfigError, match=message):
        EffectiveStrategyConfig.resolve({**BASE, field: value})


def test_missing_required_config_is_contextualized():
    with pytest.raises(ConfigError, match="invalid execution configuration"):
        EffectiveStrategyConfig.resolve({})


@pytest.mark.parametrize("version", [True, 0, 7, "6"])
def test_version_is_an_explicit_supported_integer(version):
    with pytest.raises(ConfigError, match="pine_version must be in 1..6"):
        EffectiveStrategyConfig.resolve(BASE, pine_version=version)


def test_instrument_and_host_identity_survive_detached_roundtrip():
    instrument = InstrumentModel(mode="spot", contract_size=2.0)
    values = execution_config_values(SimpleNamespace(**BASE, instrument_model=instrument))
    assert values["instrument_model"]["mode"] == "spot"
    effective = EffectiveStrategyConfig.resolve({**values, "exchange": "TEST", "market_type": "spot"})
    restored = effective.to_engine_config()
    assert restored.instrument_model == instrument
    assert restored.instrument_model is not instrument
    assert getattr(restored, "exchange") == "TEST"
    assert getattr(restored, "market_type") == "spot"
    # Direct dataclass inputs also pass the canonical identity validator.
    direct = EffectiveStrategyConfig.resolve({**BASE, "instrument_model": instrument})
    assert direct.to_engine_config().instrument_model == instrument


@pytest.mark.parametrize(
    "value, step, mode, message",
    [
        (1.0, 1.0, "guessed", "unknown rounding mode"),
        (float("nan"), 1.0, "none", "value must be finite"),
        (float("inf"), None, "nearest", "value must be finite"),
        (1.0, 0.0, "none", "step must be positive and finite"),
        (1.0, -1.0, "nearest", "step must be positive and finite"),
        (1.0, float("inf"), "nearest", "step must be positive and finite"),
    ],
)
def test_rounding_rejects_invalid_values_even_when_disabled(value, step, mode, message):
    with pytest.raises(ValueError, match=message):
        round_to_step(value, step, mode)


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("max_recalc_depth", True, "nonnegative integer"),
        ("max_recalc_depth", -1, "nonnegative integer"),
        ("qty_rounding", "unknown", "unsupported qty_rounding"),
        ("price_rounding", "none", "unsupported price_rounding"),
    ],
)
def test_engine_validation_also_protects_direct_dataclass_callers(field, value, message):
    config = BacktestConfig(**BASE)
    setattr(config, field, value)
    with pytest.raises(ConfigError, match=message):
        validate_backtest_config(config)


def test_no_provider_fallback_does_not_guess_calendar_months(monkeypatch):
    original = builtins.__import__

    def without_provider(name, *args, **kwargs):
        if name == "marketdata_provider.contracts":
            raise ImportError("optional provider deliberately absent")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_provider)
    assert infer_close_from_timeframe(1000, "1") == 61000
    with pytest.raises(BarValidationError, match="duration is unknown"):
        infer_close_from_timeframe(1000, "1M")


def test_fill_recalculation_requires_a_real_causal_fill():
    engine = SimpleNamespace(_execution_last_bar_index=0, _execution_callback_sequence=0, fills=[])
    with pytest.raises(StrategyRuntimeError, match="no causal fill"):
        describe_execution(engine, Bar(time=0, open=1, high=1, low=1, close=1), 0, fill_cause=True)


def test_unknown_exit_metadata_leg_is_not_guessed():
    with pytest.raises(ValueError, match="unknown exit leg"):
        execution_metadata(SimpleNamespace(), leg="guessed")


@dataclass
class NestedMetadata:
    value: int


def test_effective_config_report_cannot_mutate_nested_dataclass_identity():
    """Reports are detached JSON values, including nested admitted dataclasses."""
    original = NestedMetadata(1)
    effective = EffectiveStrategyConfig.resolve({**BASE, "warmup_metadata": {"row": original}})
    report = effective.report()
    row = report["values"]["warmup_metadata"]["row"]
    assert row == {"value": 1}
    row["value"] = 2
    original.value = 3
    assert effective.report()["values"]["warmup_metadata"]["row"] == {"value": 1}
