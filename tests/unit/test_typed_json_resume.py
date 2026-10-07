"""Public typed restore with literal broker/effect oracles and exporter process exit."""

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from openpine_contracts import Finality

from backtest_engine import (
    BacktestConfig,
    BacktestEngine,
    Bar,
    InstrumentModel,
    JsonResumeStateSerializer,
)
from backtest_engine.core.state_snapshot import JsonStateSerializer, StateSerializer
from backtest_engine.errors import ResumeUnsupportedError


class FixedOcaStrategy:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def run_bar(self, bar, index):
        if index == 0:
            self.ctx.order("R1", "long", qty=1, limit=99, oca_name="reduce", oca_type="reduce")
            self.ctx.order("R2", "long", qty=3, limit=90, oca_name="reduce", oca_type="reduce")
            self.ctx.order("C1", "long", qty=1, limit=98, oca_name="cancel", oca_type="cancel")
            self.ctx.order("C2", "long", qty=2, limit=89, oca_name="cancel", oca_type="cancel")

    def export_state(self):
        return {}

    def restore_state(self, state):
        assert state == {}


class BracketStrategy(FixedOcaStrategy):
    def run_bar(self, bar, index):
        if index == 0:
            self.ctx.entry("L", "long", qty=2)
            self.ctx.exit("X", "L", profit=5)


class PersistentExitStrategy(FixedOcaStrategy):
    def run_bar(self, bar, index):
        if index == 0:
            self.ctx.entry("L", "long", qty=2)
        if index == 1:
            self.ctx.exit("X", profit=5)


class NativeEntryOnly(FixedOcaStrategy):
    def run_bar(self, bar, index):
        if index == 0:
            self.ctx.entry("L", "long", qty=2)


class NativeShortOnly(FixedOcaStrategy):
    def run_bar(self, bar, index):
        if index == 0:
            self.ctx.entry("Short", "short", qty=80)


class RepeatedOrders(FixedOcaStrategy):
    def run_bar(self, bar, index):
        self.ctx.order(str(index), "long", qty=1)


class Reversal(FixedOcaStrategy):
    def run_bar(self, bar, index):
        if index == 0:
            self.ctx.entry("A", "long", qty=2)
        if index == 1:
            self.ctx.entry("B", "short", qty=3)


class PartialBracket(FixedOcaStrategy):
    def run_bar(self, bar, index):
        if index == 0:
            self.ctx.entry("L", "long", qty=2)
            self.ctx.exit("X", "L", profit=5, qty=1)


def inputs(values=None, **options):
    values = values or [(100, 100, 100, 100), (100, 101, 97, 100), (100, 101, 89, 100)]
    bars = [
        Bar(time=i * 60000, open=o, high=h, low=low, close=c, volume=1, finality=Finality.FINAL)
        for i, (o, h, low, c) in enumerate(values)
    ]
    config = BacktestConfig(
        "S",
        "1m",
        0,
        (len(bars) - 1) * 60000,
        **{
            "initial_capital": 100000,
            "commission_type": "none",
            "commission_value": 0,
            "mintick": 1,
            "force_close_on_end": False,
            "export_resume_state": True,
            "semantic_profile": "strict_5x",
            **options,
        },
    )
    return config, bars


def observe(engine):
    return {
        "orders": [(o.id, o.qty, o.status) for o in engine.orders],
        "fills": [(f.order_id, f.price, f.qty, f.commission) for f in engine.fills],
        "position_size": engine.position.size,
        "average_price": engine.position.avg_price,
        "cash": engine.cash,
        "equity": engine.equity,
        "open_trades": [(t.entry_id, t.entry_price, t.qty) for t in engine.open_trades],
        "events": [(e.code, e.order_id, e.bar_index) for e in engine.events],
        "equity_history": [(p.bar_index, p.equity) for p in engine._resume_equity_curve_history],
    }


PREFIX_EFFECTS = [
    ("ORDER_CREATED", "R1", 0),
    ("ORDER_CREATED", "R2", 0),
    ("ORDER_CREATED", "C1", 0),
    ("ORDER_CREATED", "C2", 0),
    ("ORDER_ACTIVATED", "R1", 1),
    ("ORDER_ACTIVATED", "R2", 1),
    ("ORDER_ACTIVATED", "C1", 1),
    ("ORDER_ACTIVATED", "C2", 1),
    ("ORDER_FILLED", "R1", 1),
    ("ORDER_MODIFIED", "R2", 1),
    ("ORDER_FILLED", "C1", 1),
    ("ORDER_CANCELLED", "C2", 1),
]
CUT_EXPECTED = {
    "orders": [
        ("R1", 1, "filled"),
        ("R2", 2, "active"),
        ("C1", 1, "filled"),
        ("C2", 2, "cancelled"),
    ],
    "fills": [("R1", 99, 1, 0), ("C1", 98, 1, 0)],
    "position_size": 2,
    "average_price": 98.5,
    "cash": 100000,
    "equity": 100003,
    "open_trades": [("R1", 99, 1), ("C1", 98, 1)],
    "events": PREFIX_EFFECTS,
    "equity_history": [(0, 100000), (1, 100003)],
}
CONTINUATION_EXPECTED = {
    "orders": [
        ("R1", 1, "filled"),
        ("R2", 2, "filled"),
        ("C1", 1, "filled"),
        ("C2", 2, "cancelled"),
    ],
    "fills": [("R1", 99, 1, 0), ("C1", 98, 1, 0), ("R2", 90, 2, 0)],
    "position_size": 4,
    "average_price": 94.25,
    "cash": 100000,
    "equity": 100023,
    "open_trades": [("R1", 99, 1), ("C1", 98, 1), ("R2", 90, 2)],
    "events": PREFIX_EFFECTS + [("ORDER_FILLED", "R2", 2)],
    "equity_history": [(0, 100000), (1, 100003), (2, 100023)],
}


def checkpoint():
    config, bars = inputs()
    engine = BacktestEngine(config)
    result = engine.run(FixedOcaStrategy, bars=bars[:2])
    assert result.status == "completed", result.errors
    assert observe(engine) == CUT_EXPECTED
    assert result.resume_state is not None
    return result.resume_state


def process_probe(mode, directory):
    config, bars = inputs()
    engine = BacktestEngine(config)
    if mode == "producer":
        result = engine.run(FixedOcaStrategy, bars=bars[:2])
        assert result.status == "completed", result.errors
        assert observe(engine) == CUT_EXPECTED
        state = result.resume_state
        serializer: StateSerializer = JsonResumeStateSerializer()
        (directory / "checkpoint.json").write_bytes(serializer.dumps(state))
        expected = CUT_EXPECTED
    else:
        result = engine.run(
            FixedOcaStrategy, bars=bars, resume_state=(directory / "checkpoint.json").read_bytes()
        )
        assert result.status == "completed", result.errors
        assert observe(engine) == CONTINUATION_EXPECTED
        expected = CONTINUATION_EXPECTED
        control = BacktestEngine(config)
        assert control.run(FixedOcaStrategy, bars=bars).status == "completed"
        assert observe(control) == CONTINUATION_EXPECTED
    (directory / (mode + ".json")).write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "python": sys.version,
                "gil_enabled": sys._is_gil_enabled(),
                "oracle": expected,
                "observed": observe(engine),
                "passed": True,
            },
            indent=2,
        )
        + "\n"
    )


def test_public_exporter_exits_before_new_process_restore_and_continuation(tmp_path):
    for mode in ("producer", "consumer"):
        # run() waits for the exporter to exit before starting a fresh consumer.
        with (tmp_path / (mode + ".log")).open("wb") as log:
            subprocess.run(
                [sys.executable, "-B", __file__, mode, str(tmp_path)],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=15,
            )
    producer = json.loads((tmp_path / "producer.json").read_text())
    consumer = json.loads((tmp_path / "consumer.json").read_text())
    assert producer["pid"] != consumer["pid"]
    assert producer["passed"] and consumer["passed"]
    assert producer["gil_enabled"] and consumer["gil_enabled"]


@pytest.mark.parametrize(
    "strategy,cut", [(BracketStrategy, 1), (PersistentExitStrategy, 2), (BracketStrategy, 3)]
)
def test_deferred_persistent_and_closed_trade_checkpoints_preserve_profit(strategy, cut):
    config, bars = inputs([(100, 101, 99, 100), (100, 101, 99, 100), (100, 106, 99, 100)])
    producer = BacktestEngine(config)
    exported = producer.run(strategy, bars=bars[:cut])
    assert exported.status == "completed", exported.errors
    assert exported.resume_state is not None
    if cut == 1:
        assert "X" in producer.orders[0].pending_exits
    if strategy is PersistentExitStrategy:
        assert "X" in producer._all_entry_exits
    fresh = BacktestEngine(config)
    result = fresh.run(
        strategy, bars=bars, resume_state=JsonResumeStateSerializer().dumps(exported.resume_state)
    )
    assert result.status == "completed", result.errors
    assert [(f.order_id, f.price, f.qty) for f in fresh.fills] == [("L", 100, 2), ("X:L", 105, 2)]
    assert [(t.entry_id, t.exit_id, t.qty, t.profit) for t in fresh.closed_trades] == [
        ("L", "X:L", 2, 10)
    ]
    assert not fresh.open_trades and fresh.position.size == 0
    assert fresh.cash == fresh.equity == 100010


@pytest.mark.parametrize(
    "options,effective_pre_bars",
    [
        ({}, None),
        ({"score_start_time": 60000}, 1),
        ({"score_start_time": 120000}, 3),
        ({"collect_equity_curve": False, "required_outputs": ()}, None),
    ],
)
def test_contextual_score_and_output_modes_retain_native_continuation(options, effective_pre_bars):
    config, bars = inputs(**options)
    old = BacktestEngine(config)
    exported = old.run(FixedOcaStrategy, bars=bars[:2], effective_pre_bars=effective_pre_bars)
    fresh = BacktestEngine(config)
    resumed = fresh.run(
        FixedOcaStrategy,
        bars=bars,
        effective_pre_bars=effective_pre_bars,
        resume_state=JsonResumeStateSerializer().dumps(exported.resume_state),
    )
    assert resumed.status == "completed", resumed.errors
    assert [(f.order_id, f.price, f.qty) for f in fresh.fills] == [
        ("R1", 99, 1),
        ("C1", 98, 1),
        ("R2", 90, 2),
    ]
    assert fresh.position.size == 4 and fresh.equity == 100023


def test_pruned_terminal_orders_restore_from_durable_trade_history():
    config, bars = inputs(
        [(100, 100, 100, 100)] * 45, collect_order_lifecycle=False, process_orders_on_close=True
    )
    old = BacktestEngine(config)
    cut = old.run(RepeatedOrders, bars=bars[:36])
    assert cut.status == "completed", cut.errors
    assert len(old.fills) == 36 and len(old.orders) < 36
    assert any(fill.order_id not in {order.id for order in old.orders} for fill in old.fills)
    fresh = BacktestEngine(config)
    result = fresh.run(
        RepeatedOrders, bars=bars, resume_state=JsonResumeStateSerializer().dumps(cut.resume_state)
    )
    assert result.status == "completed", result.errors
    assert [(fill.order_id, fill.bar_index, fill.price, fill.qty) for fill in fresh.fills] == [
        (str(index), index, 100, 1) for index in range(45)
    ]
    assert len(fresh.open_trades) == 45
    assert fresh.position.size == 45 and fresh.position.avg_price == 100
    assert fresh.cash == fresh.equity == 100000


def test_forced_close_ephemeral_order_roundtrip_refreshes_final_accounting():
    config, bars = inputs(
        [(100, 100, 100, 100), (100, 105, 100, 105), (110, 110, 110, 110)],
        force_close_on_end=True,
        commission_type="fixed_per_order",
        commission_value=1,
    )
    old = BacktestEngine(config)
    cut = old.run(NativeEntryOnly, bars=bars[:2])
    assert cut.status == "completed", cut.errors
    assert [(f.order_id, f.price, f.qty) for f in old.fills] == [
        ("L", 100, 2),
        ("forced_end_close", 105, 2),
    ]
    assert "forced_end_close" not in {order.id for order in old.orders}
    assert old.cash == old.equity == 100008 and old.position.realized_profit == 8
    fresh = BacktestEngine(config)
    result = fresh.run(
        NativeEntryOnly, bars=bars, resume_state=JsonResumeStateSerializer().dumps(cut.resume_state)
    )
    assert result.status == "completed", result.errors
    assert fresh.cash == fresh.equity == 100008 and fresh.position.size == 0
    assert len(fresh.closed_trades) == 1 and fresh.closed_trades[0].profit == 8
    assert [(fill.order_id, fill.price, fill.qty) for fill in fresh.fills] == [
        ("L", 100, 2),
        ("forced_end_close", 105, 2),
    ]


def test_margin_call_ephemeral_order_roundtrip_continuation():
    config, bars = inputs(
        [(10, 10, 10, 10), (10, 12, 10, 12), (12, 12, 12, 12)],
        initial_capital=1000,
        margin_short=100,
    )
    old = BacktestEngine(config)
    cut = old.run(NativeShortOnly, bars=bars[:2])
    assert cut.status == "completed", cut.errors
    assert [(f.order_id, f.price, f.qty) for f in old.fills] == [
        ("Short", 10, 80),
        ("Margin call", 12, 40),
    ]
    assert "Margin call" not in {order.id for order in old.orders}
    fresh = BacktestEngine(config)
    result = fresh.run(
        NativeShortOnly, bars=bars, resume_state=JsonResumeStateSerializer().dumps(cut.resume_state)
    )
    assert result.status == "completed", result.errors
    assert fresh.position.size == -40 and fresh.position.avg_price == 10
    assert fresh.cash == 920 and fresh.equity == 840
    assert [(t.qty, t.profit) for t in fresh.closed_trades] == [(40, -80)]


@pytest.mark.parametrize(
    "fee_type,fee_value,expected_cash,expected_profit",
    [
        ("none", 0, 100005, 5),
        ("fixed_per_order", 1, 100003, 3.5),
        ("fixed_per_contract", 1, 100002, 3),
        ("percent", 1, 100001.95, 2.95),
    ],
)
def test_partial_lot_commission_allocations_and_continuation(
    fee_type, fee_value, expected_cash, expected_profit
):
    config, bars = inputs(
        [(100, 100, 100, 100), (100, 100, 100, 100), (100, 106, 100, 100), (100, 110, 100, 110)],
        commission_type=fee_type,
        commission_value=fee_value,
    )
    old = BacktestEngine(config)
    cut = old.run(PartialBracket, bars=bars[:3])
    assert cut.status == "completed", cut.errors
    fresh = BacktestEngine(config)
    result = fresh.run(
        PartialBracket, bars=bars, resume_state=JsonResumeStateSerializer().dumps(cut.resume_state)
    )
    assert result.status == "completed", result.errors
    assert fresh.position.size == 1 and fresh.position.avg_price == 100
    assert fresh.cash == pytest.approx(expected_cash)
    assert fresh.equity == pytest.approx(expected_cash + 10)
    assert len(fresh.closed_trades) == 1
    assert fresh.closed_trades[0].profit == pytest.approx(expected_profit)


def test_reversal_opening_and_closing_share_the_same_fill_without_double_count():
    config, bars = inputs(
        [(100, 100, 100, 100), (100, 100, 100, 100), (110, 110, 110, 110), (110, 110, 110, 110)],
        commission_type="fixed_per_contract",
        commission_value=1,
    )
    old = BacktestEngine(config)
    cut = old.run(Reversal, bars=bars[:3])
    assert cut.status == "completed", cut.errors
    fresh = BacktestEngine(config)
    result = fresh.run(
        Reversal, bars=bars, resume_state=JsonResumeStateSerializer().dumps(cut.resume_state)
    )
    assert result.status == "completed", result.errors
    assert [(f.order_id, f.price, f.qty, f.commission) for f in fresh.fills] == [
        ("A", 100, 2, 2),
        ("B", 110, 5, 5),
    ]
    assert fresh.position.size == -3 and fresh.position.avg_price == 110
    assert fresh.cash == fresh.equity == 100013
    assert [(t.qty, t.profit) for t in fresh.closed_trades] == [(2, 16)]
    assert fresh.open_trades[0].commission_entry == 3


def test_inverse_instrument_uses_its_native_pnl_for_checkpoint_admission():
    config, bars = inputs(
        [(100, 100, 100, 100), (100, 100, 100, 100), (100, 106, 100, 100), (100, 110, 100, 110)],
        instrument_model=InstrumentModel(mode="inverse_futures"),
    )
    old = BacktestEngine(config)
    cut = old.run(PartialBracket, bars=bars[:3])
    assert cut.status == "completed", cut.errors
    fresh = BacktestEngine(config)
    result = fresh.run(
        PartialBracket, bars=bars, resume_state=JsonResumeStateSerializer().dumps(cut.resume_state)
    )
    assert result.status == "completed", result.errors
    assert fresh.closed_trades[0].profit == pytest.approx(1 / 100 - 1 / 105)
    assert fresh.cash == pytest.approx(100000 + 1 / 100 - 1 / 105)
    assert fresh.equity == pytest.approx(100000 + 1 / 100 - 1 / 105 + 1 / 100 - 1 / 110)


def test_legacy_complete_native_checkpoint_without_accounting_metadata_still_restores(envelope):
    del envelope["state"]["fields"]["metadata"]["items"]["native_accounting"]
    config, bars = inputs()
    engine = BacktestEngine(config)
    result = engine.run(FixedOcaStrategy, bars=bars, resume_state=json.dumps(envelope).encode())
    assert result.status == "completed", result.errors
    assert observe(engine) == CONTINUATION_EXPECTED


@pytest.mark.parametrize("origin", ["pruned", "forced", "margin"])
def test_unknown_fill_identity_still_rejected_for_each_permitted_history_origin(
    origin, monkeypatch
):
    if origin == "pruned":
        config, bars = inputs(
            [(100, 100, 100, 100)] * 36, collect_order_lifecycle=False, process_orders_on_close=True
        )
        strategy, index = RepeatedOrders, 0
    elif origin == "forced":
        config, bars = inputs([(100, 100, 100, 100), (100, 105, 100, 105)], force_close_on_end=True)
        strategy, index = NativeEntryOnly, 1
    else:
        config, bars = inputs(
            [(10, 10, 10, 10), (10, 12, 10, 12)], initial_capital=1000, margin_short=100
        )
        strategy, index = NativeShortOnly, 1
    producer = BacktestEngine(config)
    result = producer.run(strategy, bars=bars)
    envelope = json.loads(JsonResumeStateSerializer().dumps(result.resume_state))
    envelope["state"]["fields"]["broker_state"]["fields"]["fills"][index]["fields"]["order_id"] = (
        "forged"
    )
    consumer = BacktestEngine(config)

    def forbidden_reset():
        raise AssertionError("forged fill identity reached reset")

    monkeypatch.setattr(consumer, "_reset_state", forbidden_reset)
    with pytest.raises(ResumeUnsupportedError, match="identity|link|fill"):
        consumer.run(strategy, bars=bars, resume_state=json.dumps(envelope).encode())


def test_invalid_inverse_price_has_a_defined_admission_error():
    config, bars = inputs(instrument_model=InstrumentModel(mode="inverse_futures"))
    producer = BacktestEngine(config)
    result = producer.run(FixedOcaStrategy, bars=bars[:2])
    envelope = json.loads(JsonResumeStateSerializer().dumps(result.resume_state))
    context = envelope["state"]["fields"]["metadata"]["items"]["native_accounting"]["items"]
    context["mark_price"] = 0
    with pytest.raises(ResumeUnsupportedError, match="invalid state"):
        JsonResumeStateSerializer().loads(json.dumps(envelope).encode())


@pytest.mark.parametrize("fault", ["missing", "unknown", "bool", "instrument", "schema"])
def test_accounting_context_rejects_partial_or_foreign_values(envelope, fault, monkeypatch):
    context = envelope["state"]["fields"]["metadata"]["items"]["native_accounting"]["items"]
    if fault == "missing":
        del context["commission_value"]
    elif fault == "unknown":
        context["extra"] = 0
    elif fault == "bool":
        context["initial_capital"] = True
    elif fault == "schema":
        context["schema_id"] = "foreign"
    else:
        context["instrument_model"]["items"]["mode"] = "foreign"
    assert_rejected_without_live_mutation(json.dumps(envelope).encode(), monkeypatch)


@pytest.fixture
def envelope():
    return json.loads(JsonResumeStateSerializer().dumps(checkpoint()))


ROOT = ("state", "fields")
BROKER = ROOT + ("broker_state", "fields")
ORDER = BROKER + ("orders", 1, "fields")
FILL = BROKER + ("fills", 0, "fields")
TRADE = BROKER + ("open_trades", 0, "fields")
STATS = ROOT + ("statistics_state", "items")


def set_path(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    if replacement is DELETE:
        del value[path[-1]]
    else:
        value[path[-1]] = replacement


DELETE = object()
CORRUPTIONS = [
    (("version",), 2),
    (("version",), True),
    (("schema",), "foreign"),
    (("extra",), 0),
    (("state", "type"), "builtins.eval"),
    (ROOT + ("extra",), 0),
    (ROOT + ("bar_index",), True),
    (ROOT + ("bar_index",), -2),
    (ROOT + ("config_snapshot_hash",), "bad"),
    (ROOT + ("broker_state", "type"), "mapping"),
    (BROKER + ("position", "fields", "size"), -2),
    (BROKER + ("position", "fields", "size"), 3),
    (BROKER + ("position", "fields", "avg_price"), 97),
    (BROKER + ("position", "fields", "open_profit"), 4),
    (BROKER + ("position", "fields", "realized_profit"), 1),
    (BROKER + ("cash",), 100001),
    (BROKER + ("equity",), 100004),
    (BROKER + ("cash",), True),
    (BROKER + ("max_drawdown",), -1),
    (BROKER + ("last_trade_bar",), 2),
    (BROKER + ("risk_state_version",), True),
    (BROKER + ("risk_state", "items", "_allow_long"), 1),
    (BROKER + ("risk_state", "items", "_allow_short"), DELETE),
    (ORDER + ("qty",), True),
    (ORDER + ("disable_alert",), 0),
    (ORDER + ("oca_type",), "foreign"),
    (ORDER + ("created_bar_index",), -1),
    (ORDER + ("active_from_bar_index",), 3),
    (ORDER + ("reserved_qty",), 3),
    (ORDER + ("entry_fill_index",), 10),
    (ORDER + ("oca_name",), DELETE),
    (ORDER + ("extra",), 1),
    (FILL + ("bar_index",), 1.0),
    (FILL + ("bar_index",), 2),
    (FILL + ("order_id",), "absent"),
    (FILL + ("qty",), 0),
    (FILL + ("commission",), -1),
    (FILL + ("commission",), 1),
    (FILL + ("qty",), 2),
    (FILL + ("position_direction_after",), "short"),
    (TRADE + ("entry_fill_index",), 1),
    (TRADE + ("entry_qty",), None),
    (TRADE + ("entry_qty",), 2),
    (TRADE + ("commission_entry",), 1),
    (TRADE + ("exit_bar_index",), 1),
    (TRADE + ("is_open",), False),
    (STATS + ("equity_curve", 0, "fields", "bar_index"), 1),
    (STATS + ("equity_curve", 0, "type"), "mapping"),
    (STATS + ("events", 11, "fields", "bar_index"), 2),
    (STATS + ("events", 11, "fields", "severity"), "foreign"),
    (STATS + ("engine_win_trades_total",), True),
    (STATS + ("engine_gross_profit_total",), 1),
    (STATS + ("engine_closed_trade_stats_count",), DELETE),
    (STATS + ("extra",), 0),
]


def assert_rejected_without_live_mutation(payload, monkeypatch, **run_options):
    config, bars = inputs()
    engine = BacktestEngine(config)
    assert engine.run(FixedOcaStrategy, bars=bars[:2]).status == "completed"
    before = observe(engine)
    owned = (engine.position, engine.orders, engine.fills, engine.events, engine.callbacks)

    def fail_reset():
        raise AssertionError("invalid input reached live reset")

    monkeypatch.setattr(engine, "_reset_state", fail_reset)
    with pytest.raises(ResumeUnsupportedError):
        engine.run(FixedOcaStrategy, bars=bars, resume_state=payload, **run_options)
    assert observe(engine) == before
    assert all(
        old is new
        for old, new in zip(
            owned,
            (engine.position, engine.orders, engine.fills, engine.events, engine.callbacks),
            strict=True,
        )
    )


@pytest.mark.parametrize(
    "path,replacement",
    CORRUPTIONS,
    ids=[
        "/".join(map(str, path)) + "=" + ("delete" if v is DELETE else str(v))
        for path, v in CORRUPTIONS
    ],
)
def test_corrupt_complete_input_rejected_before_live_mutation(
    envelope, path, replacement, monkeypatch
):
    set_path(envelope, path, replacement)
    assert_rejected_without_live_mutation(json.dumps(envelope).encode(), monkeypatch)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_wire_rejected_before_live_mutation(envelope, token, monkeypatch):
    payload = json.dumps(envelope, separators=(",", ":")).replace(
        '"equity":100003.0', '"equity":' + token
    )
    assert token in payload
    assert_rejected_without_live_mutation(payload.encode(), monkeypatch)


@pytest.mark.parametrize("nested", [False, True])
def test_duplicate_keys_rejected_before_live_mutation(envelope, nested, monkeypatch):
    payload = json.dumps(envelope, separators=(",", ":"))
    marker = '"qty":2.0' if nested else '"version":1'
    assert marker in payload
    payload = payload.replace(marker, marker + "," + marker, 1)
    assert_rejected_without_live_mutation(payload.encode(), monkeypatch)


@pytest.mark.parametrize(
    "mutation", ["config", "prefix", "cursor", "missing-history", "score-history", "backend"]
)
def test_context_rejected_before_live_mutation(envelope, mutation, monkeypatch):
    options = {}
    if mutation == "config":
        set_path(envelope, ROOT + ("config_snapshot_hash",), "0" * 64)
    elif mutation == "prefix":
        set_path(envelope, ROOT + ("metadata", "items", "bar_prefix_fingerprint"), "0" * 64)
    elif mutation == "cursor":
        set_path(envelope, ROOT + ("bar_index",), 3)
        set_path(envelope, STATS + ("equity_curve",), [])
    elif mutation == "missing-history":
        set_path(envelope, STATS + ("equity_curve",), [])
    elif mutation == "score-history":
        set_path(
            envelope,
            STATS + ("score_equity_points",),
            envelope["state"]["fields"]["statistics_state"]["items"]["equity_curve"],
        )
    else:
        options["execution_backend"] = object()
    assert_rejected_without_live_mutation(json.dumps(envelope).encode(), monkeypatch, **options)


@pytest.mark.parametrize("cursor", [1_000_000_000, 1 << 4095], ids=["billion", "4096-bit"])
def test_sparse_history_rejects_huge_cursor_without_cursor_sized_allocation(
    envelope, cursor, monkeypatch
):
    import backtest_engine.core.resume_json as codec

    def forbidden_range(*args):
        raise AssertionError("untrusted cursor reached range allocation")

    # Module-only guard catches the old list(range(cursor + 1)) before allocation.
    monkeypatch.setattr(codec, "range", forbidden_range, raising=False)
    set_path(envelope, ROOT + ("bar_index",), cursor)
    assert_rejected_without_live_mutation(json.dumps(envelope).encode(), monkeypatch)


@pytest.mark.parametrize("collect_history", [False, True])
def test_context_rejects_huge_cursor_before_score_range(envelope, collect_history, monkeypatch):
    import backtest_engine.core.resume_json as codec

    def forbidden_range(*args):
        raise AssertionError("unavailable input cursor reached score range allocation")

    monkeypatch.setattr(codec, "range", forbidden_range, raising=False)
    set_path(envelope, ROOT + ("bar_index",), 1_000_000_000)
    set_path(envelope, STATS + ("equity_curve",), [])
    config, bars = inputs(
        score_start_time=60000,
        collect_equity_curve=collect_history,
        required_outputs=("equity_curve",) if collect_history else (),
    )
    engine = BacktestEngine(config)
    assert engine.run(FixedOcaStrategy, bars=bars[:2], effective_pre_bars=1).status == "completed"
    before = observe(engine)
    with pytest.raises(ResumeUnsupportedError, match="available input bar"):
        engine.run(FixedOcaStrategy, bars=bars, resume_state=json.dumps(envelope).encode())
    assert observe(engine) == before


def test_failed_context_admission_does_not_normalize_live_config(envelope):
    config, bars = inputs()
    config.required_outputs = ("equity_curve",)
    config.collect_equity_curve = False
    before = config.snapshot()
    engine = BacktestEngine(config)
    set_path(envelope, ROOT + ("config_snapshot_hash",), "0" * 64)
    with pytest.raises(ResumeUnsupportedError, match="config hash"):
        engine.run(FixedOcaStrategy, bars=bars, resume_state=json.dumps(envelope).encode())
    assert config.snapshot() == before


def test_valid_context_normalizes_detached_config_for_matching_producer():
    producer_config, bars = inputs(collect_equity_curve=False, required_outputs=("equity_curve",))
    producer = BacktestEngine(producer_config)
    result = producer.run(FixedOcaStrategy, bars=bars[:2])
    consumer_config, _ = inputs(collect_equity_curve=False, required_outputs=("equity_curve",))
    consumer = BacktestEngine(consumer_config)
    resumed = consumer.run(
        FixedOcaStrategy,
        bars=bars,
        resume_state=JsonResumeStateSerializer().dumps(result.resume_state),
    )
    assert resumed.status == "completed", resumed.errors
    assert consumer.equity == 100023 and consumer.position.size == 4


def test_changed_actual_bar_prefix_rejected_before_live_mutation():
    config, bars = inputs()
    wire = JsonResumeStateSerializer().dumps(checkpoint())
    engine = BacktestEngine(config)
    assert engine.run(FixedOcaStrategy, bars=bars[:2]).status == "completed"
    before = observe(engine)
    changed = [replace(bars[0], close=101, high=101), *bars[1:]]
    with pytest.raises(ResumeUnsupportedError, match="bar prefix fingerprint"):
        engine.run(FixedOcaStrategy, bars=changed, resume_state=wire)
    assert observe(engine) == before


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize(
    "corruption",
    ["missing", "unknown", "scalar", "template-id", "bar", "trailing-domain", "price-pair-policy"],
)
def test_invalid_exit_template_rejected_before_owner_restore(persistent, corruption):
    config, bars = inputs([(100, 101, 99, 100)] * 3)
    engine = BacktestEngine(config)
    strategy, cut = (PersistentExitStrategy, 2) if persistent else (BracketStrategy, 1)
    result = engine.run(strategy, bars=bars[:cut])
    envelope = json.loads(JsonResumeStateSerializer().dumps(result.resume_state))
    broker = envelope["state"]["fields"]["broker_state"]["fields"]
    row = (
        broker["all_entry_exits"]["items"]["X"]
        if persistent
        else broker["orders"][0]["fields"]["pending_exits"]["items"]["X"]
    )["items"]
    payload = row["payload"]["items"]
    if corruption == "missing":
        del payload["qty"]
    elif corruption == "unknown":
        payload["extra"] = 0
    elif corruption == "scalar":
        payload["disable_alert"] = 0
    elif corruption == "template-id":
        payload["id"] = "wrong"
    elif corruption == "bar":
        row["bar_index"] = cut
    elif corruption == "trailing-domain":
        payload["trail_offset"] = -1
    else:
        payload["price_pair_policy"] = "foreign"
    with pytest.raises(ResumeUnsupportedError):
        JsonResumeStateSerializer().loads(json.dumps(envelope).encode())


@pytest.mark.parametrize(
    "field,value",
    [
        ("exit_price", None),
        ("exit_time", None),
        ("exit_bar_index", None),
        ("entry_fill_index", 1),
        ("entry_qty", None),
        ("is_open", True),
    ],
)
def test_partial_or_mislinked_closed_trade_rejected(field, value):
    config, bars = inputs([(100, 101, 99, 100), (100, 106, 99, 100)])
    engine = BacktestEngine(config)
    result = engine.run(BracketStrategy, bars=bars)
    envelope = json.loads(JsonResumeStateSerializer().dumps(result.resume_state))
    trade = envelope["state"]["fields"]["broker_state"]["fields"]["closed_trades"][0]["fields"]
    trade[field] = value
    with pytest.raises(ResumeUnsupportedError):
        JsonResumeStateSerializer().loads(json.dumps(envelope).encode())


@pytest.mark.parametrize("limit", [{"max_bytes": 128}, {"max_depth": 5}, {"max_items": 10}])
def test_codec_resource_limits_bound_export_and_admission(limit):
    state = checkpoint()
    payload = JsonResumeStateSerializer().dumps(state)
    with pytest.raises(ResumeUnsupportedError, match="budget"):
        JsonResumeStateSerializer(**limit).loads(payload)
    with pytest.raises(ResumeUnsupportedError, match="budget"):
        JsonResumeStateSerializer(**limit).dumps(state)


@pytest.mark.parametrize(
    "limits",
    [{"max_bytes": True}, {"max_depth": 0}, {"max_items": 200001}, {"max_bytes": 16777217}],
)
def test_limits_cannot_disable_hard_bounds(limits):
    with pytest.raises(ValueError):
        JsonResumeStateSerializer(**limits)


@pytest.mark.parametrize("payload", [b"\xff", b"[]", b"null", b"{}", b"[" * 65 + b"]" * 65])
def test_malformed_or_deep_payload_rejected(payload):
    with pytest.raises(ResumeUnsupportedError):
        JsonResumeStateSerializer().loads(payload)


@dataclass
class ForeignState:
    value: int = 1


@pytest.mark.parametrize("value", [ForeignState(), {1: "key"}, 1 << 4096, float("nan")])
def test_export_rejects_foreign_and_out_of_domain_state(value):
    with pytest.raises(ResumeUnsupportedError):
        JsonResumeStateSerializer().dumps(replace(checkpoint(), strategy_state=value))


def test_export_rejects_cycles():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ResumeUnsupportedError, match="cyclic"):
        JsonResumeStateSerializer().dumps(replace(checkpoint(), strategy_state=cycle))


def test_typed_codec_preserves_primitives_without_interpreting_user_mapping_tags():
    primitives = {
        "type": "builtins.eval",
        "items": [False, 0, "", None, 1 << 128],
        "tuple": (0, False, None),
        "quoted": '["escaped\\"braces{}"]',
    }
    codec = JsonResumeStateSerializer()
    loaded = codec.loads(codec.dumps(replace(checkpoint(), strategy_state=primitives)))
    assert loaded.strategy_state == primitives
    assert type(loaded.strategy_state["items"][0]) is bool
    assert type(loaded.strategy_state["items"][1]) is int
    assert type(loaded.strategy_state["tuple"]) is tuple
    assert JsonStateSerializer.serializer_id == "json-v1"
    assert JsonStateSerializer().loads(b'{"type":"ordinary","value":false}') == {
        "type": "ordinary",
        "value": False,
    }


if __name__ == "__main__":
    process_probe(sys.argv[1], Path(sys.argv[2]))
