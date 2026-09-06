"""Adopt the published 2.6 contract, never a conflicting private wire dialect."""

import pytest
from openpine_contracts import validate_payload
from backtest_engine.core.intent_replay import (
    UnsupportedIntentError, validate_intent_tape, admit_sealed_intent_tape,
)
from tests.unit.test_intent_replay import _event
from tests.unit.test_strategy_host_surface import handler, transaction, dispatch, seal


@pytest.mark.parametrize("field", ["comment_profit", "comment_loss", "comment_trailing",
                                  "alert_profit", "alert_loss", "alert_trailing"])
@pytest.mark.parametrize("version", [5, 6])
def test_handler_emits_published_flat_metadata_contract(field, version):
    h = handler(version)
    tx = transaction(h, version)
    dispatch(tx, "strategy.exit", ("X", "A"), {"limit": 110, field: ""})
    (event,) = seal(h, tx)
    assert event["schema_version"] == "2.6.0" and event[field] == ""
    assert event["price_pair_policy"] == ("absolute_first" if version == 5 else "first_trigger")
    assert "exit_metadata" not in event and "exit_semantics_version" not in event
    validate_payload("openpine.intent.v2", event)


@pytest.mark.parametrize("admit", [validate_intent_tape, admit_sealed_intent_tape])
@pytest.mark.parametrize("field", ["stop", "loss"])
def test_valid_but_unimplemented_composite_stop_fails_in_both_admission_paths(admit, field):
    event = _event(0, kind="exit", schema_version="2.6.0", price_pair_policy="first_trigger",
                   trail_points="5", trail_offset="2", **{field: "1"})
    validate_payload("openpine.intent.v2", event)
    with pytest.raises(UnsupportedIntentError, match="fixed stop plus trailing"):
        admit([event])
