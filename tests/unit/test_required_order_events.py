"""Explicit output requirements must control collection, not just advertising."""

from dataclasses import asdict
import pytest
from tests.unit.test_deferred_market_exits import candles, run


def execute(*, collect_events, outputs, disabled=False):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=1, alert_message="entry", disable_alert=disabled)
            ctx.exit(
                "X",
                "A",
                limit=105,
                comment_profit="take-profit",
                alert_profit="exit",
                disable_alert=disabled,
            )

    return run(
        commands,
        candles((100, 101, 99, 100), (100, 110, 99, 104)),
        collect_events=collect_events,
        required_outputs=outputs,
    )


@pytest.mark.parametrize("disabled", [False, True])
def test_required_order_events_matches_full_collection(disabled):
    engine, required = execute(collect_events=False, outputs={"order_events"}, disabled=disabled)
    _, full = execute(collect_events=True, outputs=set(), disabled=disabled)
    assert required.status == full.status == "completed"
    assert "order_events" in required.available_outputs
    assert len([event for event in required.events if event.code == "ORDER_FILLED"]) == 2
    assert [asdict(event) for event in required.events] == [asdict(event) for event in full.events]
    assert len(engine.fills) == 2
    assert all(
        event.context["alert_eligible"] is (not disabled)
        for event in required.events
        if event.code == "ORDER_FILLED"
    )


def test_unrequested_events_are_not_materialized():
    engine, result = execute(collect_events=False, outputs=set())
    assert result.status == "completed"
    assert result.events is None and not engine.events
    assert "order_events" not in result.available_outputs
    assert len(engine.fills) == 2


def test_no_orders_is_a_valid_empty_requested_event_output():
    engine, result = run(
        lambda ctx, i: None,
        candles((100, 101, 99, 100)),
        collect_events=False,
        required_outputs={"order_events"},
    )
    assert result.status == "completed"
    assert result.events == [] and "order_events" in result.available_outputs
