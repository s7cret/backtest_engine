"""Canonical entry risk intents use the existing RiskIntent wire, without order fallbacks."""

import pytest
from pinelib.errors import PineRuntimeError
from tests.unit.test_strategy_host_surface import handler, transaction, dispatch, seal


@pytest.mark.parametrize(
    "name,value,rule,unit,amount",
    [
        ("strategy.risk.max_position_size", 0, "max_position_size", "fixed", "0"),
        ("strategy.risk.max_position_size", 2.5, "max_position_size", "fixed", "2.5"),
        ("strategy.risk.allow_entry_in", "strategy.direction.long", "allow_entry_in", "long", "0"),
        (
            "strategy.risk.allow_entry_in",
            "strategy.direction.short",
            "allow_entry_in",
            "short",
            "0",
        ),
        ("strategy.risk.allow_entry_in", "strategy.direction.all", "allow_entry_in", "all", "0"),
    ],
)
def test_entry_risk_seals_exact_scope_and_units(name, value, rule, unit, amount):
    h = handler()
    tx = transaction(h)
    dispatch(tx, name, (value,))
    (event,) = seal(h, tx)
    assert event["kind"] == "risk" and event["schema_version"] == "2.2.0"
    assert (event["risk_rule"], event["risk_unit"], event["risk_value"], event["risk_scope"]) == (
        rule,
        unit,
        amount,
        "strategy",
    )
    assert event["command_id"] == name


@pytest.mark.parametrize("value", [None, True, -1, float("inf"), float("nan"), "3"])
def test_invalid_risk_values_never_become_zero_size_orders(value):
    h = handler()
    tx = transaction(h)
    with pytest.raises((PineRuntimeError, ValueError)):
        dispatch(tx, "strategy.risk.max_position_size", (value,))
        seal(h, tx)


@pytest.mark.parametrize("version", range(1, 7))
def test_risk_calls_never_accept_historical_when(version):
    from backtest_engine.core.strategy_capabilities import STRATEGY_COMMANDS

    with pytest.raises(ValueError):
        STRATEGY_COMMANDS["strategy.risk.max_position_size"].bind([2], {"when": False}, version)
