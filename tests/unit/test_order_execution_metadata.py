"""Actual broker fills select metadata once; amendments cannot rewrite history."""

from copy import deepcopy
from dataclasses import asdict, replace
import pytest
from backtest_engine import BacktestEngine
from backtest_engine.context.command_buffer import ExitPayload, EntryOrderPayload, ClosePayload
from backtest_engine.core.order_metadata import EXIT_METADATA_FIELDS
from backtest_engine.core.strategy_projection import build_strategy_ledger_projection
from tests.unit.test_deferred_market_exits import run, candles
from tests.unit.test_all_entry_exit_scope import mirror


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("leg", ["profit", "loss", "trailing"])
@pytest.mark.parametrize("override", [None, "", "specific"])
@pytest.mark.parametrize("disabled", [False, True])
def test_fill_and_trade_have_selected_leg_metadata(direction, leg, override, disabled):
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", direction, qty=2, comment="entry", alert_message="enter")
            prices = (
                {"limit": 105 if direction == "long" else 95}
                if leg == "profit"
                else {"stop": 95 if direction == "long" else 105}
                if leg == "loss"
                else {"trail_points": 5, "trail_offset": 2}
            )
            ctx.exit(
                "X",
                "A",
                **prices,
                comment="common",
                alert_message="common-alert",
                disable_alert=disabled,
                **{"comment_" + leg: override, "alert_" + leg: override},
            )

    path = [(100, 101, 99, 100), (100, 110, 94, 106), (106, 110, 100, 104)]
    engine, result = run(commands, mirror(candles(*path), direction))
    assert result.status == "completed", result.errors
    (trade,) = result.closed_trades
    fill = engine.fills[-1]
    assert fill.exit_leg == leg
    assert fill.comment == trade.exit_comment == ("common" if override is None else override)
    assert fill.alert_message == ("common-alert" if override is None else override)
    assert fill.disable_alert is disabled
    assert trade.entry_comment == "entry"
    assert trade.entry_alert_message == "enter"
    assert trade.exit_alert_message == fill.alert_message
    assert trade.exit_disable_alert is disabled
    assert trade.exit_leg == leg
    assert engine.fills[0].alert_message == "enter"
    assert fill.parent_exit_id == "X"
    projection = build_strategy_ledger_projection(engine)
    assert projection["closed_trade_log"][0]["exit_comment"] == trade.exit_comment


@pytest.mark.parametrize("kind", ["entry", "order", "close", "close_all"])
def test_every_order_command_keeps_alert_controls(kind):
    def commands(ctx, i):
        if i == 0:
            getattr(ctx, kind if kind in {"entry", "order"} else "entry")(
                "A", "long", qty=1, comment="opening", alert_message="open-msg", disable_alert=True
            )
        if i == 1:
            if kind == "close_all":
                ctx.close_all(comment="closing", alert_message="close-msg", disable_alert=True)
            else:
                ctx.close("A", comment="closing", alert_message="close-msg", disable_alert=True)

    engine, result = run(
        commands, candles((100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100))
    )
    assert result.status == "completed", result.errors
    assert [f.alert_message for f in engine.fills] == ["open-msg", "close-msg"]
    assert all(f.disable_alert for f in engine.fills)
    assert result.closed_trades[0].entry_comment == "opening"
    assert result.closed_trades[0].exit_comment == "closing"


def test_amendment_keeps_single_order_and_applies_latest_metadata():
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=1, limit=95, alert_message="old-entry")
            ctx.entry("A", "long", qty=1, limit=99, alert_message="entry")
            ctx.exit(
                "X",
                "A",
                limit=110,
                comment_profit="old",
                alert_profit="old-alert",
                disable_alert=True,
            )
        if i == 1:
            ctx.exit("X", "A", limit=110, comment_profit="", alert_profit="", disable_alert=False)

    e, result = run(
        commands, candles((100, 101, 99, 100), (100, 105, 98, 100), (100, 112, 99, 100))
    )
    assert result.status == "completed", result.errors
    assert len([o for o in e.orders if o.kind == "exit"]) == 1
    assert e.fills[0].alert_message == "entry"
    assert e.fills[-1].comment == e.fills[-1].alert_message == ""
    assert not e.fills[-1].disable_alert


def test_reused_or_mutated_order_ids_do_not_rewrite_execution_comments():
    def commands(ctx, i):
        if i in (0, 2):
            ctx.entry("A", "long", qty=1, comment=f"entry-{i}")
        if i in (1, 3):
            ctx.close("A", comment=f"exit-{i}")

    e, result = run(commands, candles(*[(100, 101, 99, 100)] * 5))
    before = build_strategy_ledger_projection(e)
    for order in e.orders:
        order.comment = "later mutation"
    after = build_strategy_ledger_projection(e)
    assert [(t.entry_comment, t.exit_comment) for t in result.closed_trades] == [
        ("entry-0", "exit-1"),
        ("entry-2", "exit-3"),
    ]
    assert before["closed_trade_log"] == after["closed_trade_log"]
    assert [f.comment for f in e.fills] == ["entry-0", "exit-1", "entry-2", "exit-3"]


@pytest.mark.parametrize("field", ["comment", "alert_message", *EXIT_METADATA_FIELDS])
@pytest.mark.parametrize("value", [False, 2, {}, []])
def test_invalid_metadata_is_rejected_before_buffering(field, value):
    with pytest.raises(ValueError):
        ExitPayload("X", **{field: value})


@pytest.mark.parametrize("payload", [EntryOrderPayload, ClosePayload, ExitPayload])
@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_disable_alert_never_uses_python_truthiness(payload, value):
    arguments = {"id": "A", "disable_alert": value}
    if payload is EntryOrderPayload:
        arguments["direction"] = "long"
    with pytest.raises(ValueError):
        payload(**arguments)


def test_cancelled_leg_does_not_generate_a_fill_metadata_record():
    def commands(ctx, i):
        if i == 0:
            ctx.entry("A", "long", qty=1)
            ctx.exit("X", "A", limit=105, alert_profit="must-not-fire")
            ctx.cancel("X")

    e, r = run(commands, candles((100, 101, 99, 100), (100, 110, 99, 100)))
    assert r.status == "completed" and not r.closed_trades
    assert len(e.fills) == 1 and e.fills[0].alert_message is None


def test_native_resume_retains_resolved_and_persistent_metadata():
    from tests.unit.test_p1_deterministic_tick_replay import _legacy_config

    class Strategy:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx
            self.sent = set()

        def run_bar(self, bar, bar_index):
            if bar_index in self.sent:
                return
            self.sent.add(bar_index)
            if bar_index == 0:
                self.ctx.entry("A", "long", qty=2, comment="entry A")
                self.ctx.exit(
                    "X",
                    limit=120,
                    comment="common",
                    comment_profit="TP",
                    alert_profit="tp-alert",
                    disable_alert=True,
                )
            if bar_index == 1:
                self.ctx.entry("B", "long", qty=3, comment="entry B")

        def export_state(self):
            return {"sent": sorted(self.sent)}

        def restore_state(self, state):
            self.sent = set(state["sent"])

    rows = candles((100, 101, 99, 100), (100, 101, 99, 100), (110, 121, 109, 115))
    cfg = replace(_legacy_config(rows), pyramiding=2, mintick=1, force_close_on_end=False)
    whole = BacktestEngine(cfg)
    expected = whole.run(Strategy, bars=rows)
    prefix = BacktestEngine(cfg).run(Strategy, bars=rows[:2])
    saved = deepcopy(prefix.resume_state)
    resumed = BacktestEngine(cfg)
    actual = resumed.run(Strategy, bars=rows, resume_state=saved)
    assert actual.status == expected.status == "completed"
    assert expected.closed_trades == actual.closed_trades and whole.fills == resumed.fills
    assert [asdict(f)["alert_message"] for f in resumed.fills if f.parent_exit_id == "X"] == [
        "tp-alert",
        "tp-alert",
    ]
