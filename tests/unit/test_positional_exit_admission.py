"""Archived binding fix uses the published metadata dialect after NA normalization."""
import pytest
from tests.unit.test_strategy_host_surface import handler, transaction, dispatch, seal
from pinelib import na
from pinelib.errors import PineRuntimeError

@pytest.mark.parametrize("version", [5, 6])
def test_na_slots_do_not_create_a_trailing_conflict(version):
    h = handler(version)
    tx = transaction(h, version)
    dispatch(tx, "strategy.exit", ("X", "A"), dict(limit=105, stop=na,
        trail_price=na, trail_points=na, trail_offset=na, comment_profit="TP"))
    (event,) = seal(h, tx)
    assert event["schema_version"] == "2.6.0"
    assert event["comment_profit"] == "TP" and "exit_metadata" not in event
    assert "stop" not in event and "trail_offset" not in event

def test_v6_positional_comment_has_no_phantom_oca_type_slot():
    h = handler(6)
    tx = transaction(h, 6)
    dispatch(tx, "strategy.exit", ("X", "A", na, na, 5, na, na, na, na, na, na, "group", "text"), {})
    (event,) = seal(h, tx)
    assert event["comment"] == "text" and event["oca_name"] == "group"
    assert "oca_type" not in event

@pytest.mark.parametrize("named", [dict(trail_price=105), dict(trail_offset=2),
    dict(trail_price=105, trail_offset=2, stop=95)])
def test_dynamic_active_shapes_are_still_rejected(named):
    h = handler(6)
    tx = transaction(h, 6)
    dispatch(tx, "strategy.exit", ("X", "A"), named)
    with pytest.raises(PineRuntimeError):
        seal(h, tx)
