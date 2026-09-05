"""OP-03: settings and quantity semantics survive the worker boundary."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from backtest_engine.config import BacktestConfig
from backtest_engine.config_transport import EffectiveStrategyConfig, execution_config_values
from backtest_engine.errors import ConfigError
from backtest_engine.broker.rounding import round_to_step
from backtest_engine.core.intent_replay import decimal_to_engine_number

BASE = {"symbol": "BTCUSDT", "timeframe": "1m", "start_time": 0, "end_time": 60000}


def test_transport_preserves_zero_false_currency_and_collection_settings():
    raw = BacktestConfig(
        **BASE,
        initial_capital=0,
        default_qty_value=0,
        margin_long=0,
        margin_short=0,
        commission_value=0,
        allow_short=False,
        currency="EUR",
        collect_equity_curve=False,
        min_pre_bars=0,
        auto_pre_bars=False,
    )
    effective = EffectiveStrategyConfig.resolve(execution_config_values(raw))
    actual = effective.to_engine_config()
    for field in (
        "initial_capital",
        "default_qty_value",
        "margin_long",
        "margin_short",
        "commission_value",
    ):
        assert getattr(actual, field) == 0
    assert actual.allow_short is False
    assert actual.collect_equity_curve is False
    assert actual.currency == "EUR"
    assert (
        effective.content_hash
        == EffectiveStrategyConfig.resolve(execution_config_values(actual)).content_hash
    )


@pytest.mark.parametrize("version,margin", [(1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 100)])
def test_versioned_margin_defaults_and_explicit_zero(version, margin):
    value = EffectiveStrategyConfig.resolve(BASE, pine_version=version)
    assert value.to_engine_config().margin_long == margin
    assert value.origins["margin_long"] == f"pine_v{version}_default"
    assert (
        EffectiveStrategyConfig.resolve({**BASE, "margin_long": 0}, pine_version=version)
        .to_engine_config()
        .margin_long
        == 0
    )


def test_provenance_override_priority_and_detached_configuration():
    value = EffectiveStrategyConfig.resolve(
        BASE, declaration={"default_qty_value": 3}, overrides={"default_qty_value": 7}
    )
    assert value.origins["default_qty_value"] == "user_override"
    config = value.to_engine_config()
    config.default_qty_value = 999
    report = value.report()
    report["values"]["default_qty_value"] = 99
    assert value.to_engine_config().default_qty_value == 7
    with pytest.raises(TypeError):
        value._values["default_qty_value"] = 8
    with pytest.raises(FrozenInstanceError):
        value.content_hash = "changed"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("margin_long", -1),
        ("margin_short", float("nan")),
        ("margin_long", None),
        ("default_qty_value", True),
        ("calc_on_order_fills", "false"),
        ("qty_rounding", "magic"),
        ("unsupported_option", 1),
        ("qty_step", 0),
    ],
)
def test_invalid_values_are_rejected_not_replaced(field, bad):
    with pytest.raises(ConfigError):
        EffectiveStrategyConfig.resolve({**BASE, field: bad})


@pytest.mark.parametrize(
    "mode,expected",
    [("floor", 1.25), ("ceil", 1.5), ("nearest", 1.25), ("none", 1.37), ("truncate", 1.25)],
)
def test_rounding_respects_non_decimal_power_steps(mode, expected):
    assert round_to_step(1.37, 0.25, mode) == expected
    config = BacktestConfig(**BASE, qty_step=0.25, qty_rounding=mode)
    assert (
        decimal_to_engine_number("1.37", field="qty", ctx=SimpleNamespace(config=config))
        == expected
    )


def test_transport_keeps_no_rounding_explicit():
    source = SimpleNamespace(**BASE, qty_step=0.25, qty_rounding_mode="none")
    config = EffectiveStrategyConfig.resolve(execution_config_values(source)).to_engine_config()
    assert config.qty_step == 0.25 and config.qty_rounding == "none"


def test_quantity_changes_have_distinct_identity():
    assert (
        EffectiveStrategyConfig.resolve({**BASE, "default_qty_value": 2}).content_hash
        != EffectiveStrategyConfig.resolve({**BASE, "default_qty_value": 3}).content_hash
    )
