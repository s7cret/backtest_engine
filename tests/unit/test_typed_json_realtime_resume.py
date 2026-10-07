"""Committed tick bytes restore through trusted owner preflight and native replay."""

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from openpine_contracts import Finality

from backtest_engine import BacktestEngine, Bar, JsonResumeStateSerializer
from backtest_engine.errors import ResumeUnsupportedError
from tests.unit.test_p1_deterministic_tick_replay import (
    SyntheticRealtimeRuntime,
    TrailingAndReversalStrategy,
    _config,
    _lifecycle_fixture,
)


class CheckedRuntime(SyntheticRealtimeRuntime):
    def end_bar(self):
        super().end_bar()
        self.current_bar = replace(self.current_bar, finality=Finality.FINAL)

    @staticmethod
    def validate_resume_state(state, *, bar_index, committed_bar):
        if type(state) is not dict or set(state) != {
            "normal",
            "varip",
            "current_bar",
            "ended_bars",
        }:
            raise ValueError("complete runtime fields required")
        if type(state["normal"]) is not int or state["normal"] < 0:
            raise ValueError("normal must be a nonnegative exact int")
        if type(state["ended_bars"]) is not int or state["ended_bars"] != bar_index + 1:
            raise ValueError("runtime does not represent the committed cursor")
        bar = state["current_bar"]
        if type(bar) is not Bar or bar != replace(committed_bar, finality=Finality.FINAL):
            raise ValueError("required current_bar must be the typed committed Bar")
        varip = state["varip"]
        if type(varip) is not dict:
            raise ValueError("varip must be a mapping")
        submitted = {"submitted:0": 0, "submitted:2": 2, "submitted:3": 3}
        for key, value in varip.items():
            if key not in submitted or submitted[key] > bar_index or type(value) is not bool:
                raise ValueError("late varip field is outside this runtime's schema")


class CheckedStrategy(TrailingAndReversalStrategy):
    realtime_resume_runtime = "config"

    @staticmethod
    def validate_resume_state(state, *, bar_index, committed_bar):
        del bar_index, committed_bar
        if type(state) is not dict or set(state) != {"ordinary"}:
            raise ValueError("complete strategy fields required")
        if type(state["ordinary"]) is not int or state["ordinary"] < 0:
            raise ValueError("ordinary must be a nonnegative exact int")


def scenario(cut=False):
    bars, ticks = _lifecycle_fixture()
    bars = [replace(bar, finality=Finality.FINAL) for bar in bars]
    selected = bars[:2] if cut else bars
    ticks = [tick for tick in ticks if tick.time < selected[-1].time_close]
    config = _config(
        bars,
        ticks,
        CheckedRuntime(),
        initial_capital=100000,
        export_resume_state=True,
        force_close_on_end=False,
    )
    return config, selected


def observed(engine):
    return {
        "fills": [(fill.order_id, fill.price, fill.qty, fill.commission) for fill in engine.fills],
        "fill_effects": [
            (event.order_id, event.bar_index)
            for event in engine.events
            if event.code == "ORDER_FILLED"
        ],
        "size": engine.position.size,
        "average": engine.position.avg_price,
        "cash": engine.cash,
        "equity": engine.equity,
        "closed": [
            (trade.entry_id, trade.exit_id, trade.qty, trade.profit)
            for trade in engine.closed_trades
        ],
        "open": [(trade.entry_id, trade.qty, trade.entry_price) for trade in engine.open_trades],
        "history": [
            (point.bar_index, point.equity) for point in engine._resume_equity_curve_history
        ],
        "ended_bars": engine.config.runtime.ended_bars,
        "runtime_bar": (
            engine.config.runtime.current_bar.time,
            engine.config.runtime.current_bar.close,
            engine.config.runtime.current_bar.finality.value,
        ),
        "varip": engine.config.runtime.varip,
    }


CUT = {
    "fills": [("L", 10, 1, 0), ("TR:T", 11, 1, 0)],
    "fill_effects": [("L", 0), ("TR:T", 1)],
    "size": 0,
    "average": 0,
    "cash": 100001,
    "equity": 100001,
    "closed": [("L", "TR:T", 1, 1)],
    "open": [],
    "history": [(0, 100000), (1, 100001)],
    "ended_bars": 2,
    "runtime_bar": (160, 11, "FINAL"),
    "varip": {"submitted:0": True},
}
FULL = {
    "fills": [("L", 10, 1, 0), ("TR:T", 11, 1, 0), ("S", 10, 1, 0), ("L2", 11, 2, 0)],
    "fill_effects": [("L", 0), ("TR:T", 1), ("S", 2), ("L2", 3)],
    "size": 1,
    "average": 11,
    "cash": 100000,
    "equity": 100000,
    "closed": [("L", "TR:T", 1, 1), ("S", "L2", 1, -1)],
    "open": [("L2", 1, 11)],
    "history": [(0, 100000), (1, 100001), (2, 100001), (3, 100000)],
    "ended_bars": 4,
    "runtime_bar": (280, 11, "FINAL"),
    "varip": {"submitted:0": True, "submitted:2": True, "submitted:3": True},
}


def export_cut():
    config, bars = scenario(cut=True)
    engine = BacktestEngine(config)
    result = engine.run(CheckedStrategy, bars=bars)
    assert result.status == "completed", result.errors
    assert observed(engine) == CUT
    return result.resume_state


def process_probe(mode, directory):
    config, bars = scenario(cut=mode == "producer")
    engine = BacktestEngine(config)
    if mode == "producer":
        result = engine.run(CheckedStrategy, bars=bars)
        payload = JsonResumeStateSerializer().dumps(result.resume_state)
        (directory / "checkpoint.json").write_bytes(payload)
        expected = CUT
    else:
        payload = (directory / "checkpoint.json").read_bytes()
        decoded = JsonResumeStateSerializer().loads(payload)
        assert type(decoded.runtime_state["current_bar"]) is Bar
        assert decoded.runtime_state["current_bar"].finality is Finality.FINAL
        result = engine.run(CheckedStrategy, bars=bars, resume_state=payload)
        expected = FULL
    assert result.status == "completed", result.errors
    assert observed(engine) == expected
    (directory / (mode + ".json")).write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "gil_enabled": sys._is_gil_enabled(),
                "observed": observed(engine),
                "oracle": expected,
                "passed": True,
            },
            indent=2,
        )
        + "\n"
    )


def test_new_process_committed_tick_roundtrip_and_native_continuation(tmp_path):
    for mode in ("producer", "consumer"):
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


@pytest.mark.parametrize("container", [list, tuple])
def test_explicit_schedule_is_resolved_once_and_reused(container, monkeypatch):
    import backtest_engine.core.resume_realtime as admission
    import backtest_engine.core.realtime_run_loop as replay

    config, bars = scenario()
    partial_config, partial_bars = scenario(cut=True)
    config.realtime_ticks = container(config.realtime_ticks)
    partial_config.realtime_ticks = container(partial_config.realtime_ticks)
    producer = BacktestEngine(partial_config)
    state = producer.run(CheckedStrategy, bars=partial_bars).resume_state
    payload = JsonResumeStateSerializer().dumps(state)
    original, calls = admission.resolve_realtime_tick_schedule, []

    def counted(config, series):
        assert type(config.realtime_ticks) is tuple
        calls.append(True)
        return original(config, series)

    def forbidden(*args):
        raise AssertionError("admitted schedule was resolved again after reset")

    monkeypatch.setattr(admission, "resolve_realtime_tick_schedule", counted)
    monkeypatch.setattr(replay, "resolve_realtime_tick_schedule", forbidden)
    consumer = BacktestEngine(config)
    assert consumer.run(CheckedStrategy, bars=bars, resume_state=payload).status == "completed"
    assert calls == [True] and observed(consumer) == FULL


@pytest.fixture
def wire():
    return json.loads(JsonResumeStateSerializer().dumps(export_cut()))


RUNTIME = ("state", "fields", "runtime_state", "items")
BAR = RUNTIME + ("current_bar", "fields")
STRATEGY = ("state", "fields", "strategy_state", "items")
META = ("state", "fields", "metadata", "items")


def set_value(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


def reject_preserving_live_owners(payload, monkeypatch, *, schedule_needed=False, ticks=None):
    import backtest_engine.core.resume_realtime as admission

    config, bars = scenario()
    # Seed a real live prefix using its complete explicit tick source.
    partial_config, partial_bars = scenario(cut=True)
    engine = BacktestEngine(partial_config)
    assert engine.run(CheckedStrategy, bars=partial_bars).status == "completed"
    before = observed(engine)
    runtime_before = engine.config.runtime.export_state()
    owners = (
        engine.position,
        engine.orders,
        engine.fills,
        engine.events,
        engine.callbacks,
        engine.config.runtime,
        engine.config.runtime.current_bar,
        engine.config.runtime.varip,
    )
    engine.config.realtime_ticks = config.realtime_ticks if ticks is None else ticks

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid payload reached live operation or schedule allocation")

    def forbidden_export(*, include_varip=True):
        del include_varip
        raise AssertionError("invalid payload reached live runtime export")

    monkeypatch.setattr(engine, "_reset_state", forbidden)
    monkeypatch.setattr(CheckedStrategy, "__init__", forbidden)
    monkeypatch.setattr(engine.config.runtime, "restore_state", forbidden)
    monkeypatch.setattr(engine.config.runtime, "export_state", forbidden_export)
    if not schedule_needed:
        monkeypatch.setattr(admission, "resolve_realtime_tick_schedule", forbidden)
    with pytest.raises(ResumeUnsupportedError):
        engine.run(CheckedStrategy, bars=bars, resume_state=payload)
    assert observed(engine) == before
    assert SyntheticRealtimeRuntime.export_state(engine.config.runtime) == runtime_before
    assert all(
        old is new
        for old, new in zip(
            owners,
            (
                engine.position,
                engine.orders,
                engine.fills,
                engine.events,
                engine.callbacks,
                engine.config.runtime,
                engine.config.runtime.current_bar,
                engine.config.runtime.varip,
            ),
            strict=True,
        )
    )


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("state", "fields", "runtime_state"), None),
        (("state", "fields", "strategy_state"), None),
        (META + ("realtime_resume_boundary",), "provisional"),
        (META + ("realtime_resume_boundary",), None),
        (RUNTIME + ("current_bar",), None),
        (RUNTIME + ("normal",), True),
        (RUNTIME + ("ended_bars",), True),
        (RUNTIME + ("ended_bars",), 1),
        (RUNTIME + ("pending_abort",), {"type": "mapping", "items": {"late": 1}}),
        (RUNTIME + ("provisional",), True),
        (RUNTIME + ("varip", "items", "late"), {"type": "mapping", "items": {"bad": 1}}),
        (RUNTIME + ("varip", "items", "submitted:0"), 1),
        (STRATEGY + ("ordinary",), True),
        (STRATEGY + ("late",), 1),
        (BAR + ("time",), True),
        (BAR + ("time",), 160.0),
        (BAR + ("time_close",), 159),
        (BAR + ("close",), True),
        (BAR + ("high",), 9),
        (BAR + ("low",), 13),
        (BAR + ("volume",), -1),
        (BAR + ("volume",), True),
        (BAR + ("volume",), 2),
        (BAR + ("extra",), 0),
        (BAR + ("finality",), "FINAL"),
        (BAR + ("finality",), {"type": "Finality", "value": "UNKNOWN"}),
        (BAR + ("finality",), {"type": "Finality", "value": "FINAL", "extra": 0}),
        (BAR + ("finality",), {"type": "builtins.eval", "value": "FINAL"}),
    ],
)
def test_early_and_late_payload_corruption_preserves_live_owners(
    wire, path, replacement, monkeypatch
):
    set_value(wire, path, replacement)
    reject_preserving_live_owners(json.dumps(wire).encode(), monkeypatch)


def test_generic_mapping_cannot_fill_required_typed_bar_slot(wire, monkeypatch):
    runtime = wire["state"]["fields"]["runtime_state"]["items"]
    runtime["current_bar"] = {"type": "mapping", "items": runtime["current_bar"]["fields"]}
    payload = json.dumps(wire).encode()
    assert type(JsonResumeStateSerializer().loads(payload).runtime_state["current_bar"]) is dict
    reject_preserving_live_owners(payload, monkeypatch)


@pytest.mark.parametrize(
    "field", ["realtime_tick_schedule_fingerprint", "realtime_resume_boundary"]
)
def test_missing_tick_commit_identity_rejects_early(wire, field, monkeypatch):
    del wire["state"]["fields"]["metadata"]["items"][field]
    reject_preserving_live_owners(json.dumps(wire).encode(), monkeypatch)


def test_wrong_processed_tick_fingerprint_rejected_before_reset(wire, monkeypatch):
    wire["state"]["fields"]["metadata"]["items"]["realtime_tick_schedule_fingerprint"] = "0" * 64
    reject_preserving_live_owners(json.dumps(wire).encode(), monkeypatch, schedule_needed=True)


def test_changed_processed_ticks_with_same_parent_ohlcv_rejected_before_reset(wire, monkeypatch):
    config, _ = scenario()
    ticks = list(config.realtime_ticks)
    ticks[0] = replace(ticks[0], time=101)
    reject_preserving_live_owners(
        json.dumps(wire).encode(), monkeypatch, schedule_needed=True, ticks=ticks
    )


def test_huge_committed_cursor_rejected_before_schedule(wire, monkeypatch):
    wire["state"]["fields"]["bar_index"] = 1_000_000_000
    wire["state"]["fields"]["statistics_state"]["items"]["equity_curve"] = []
    reject_preserving_live_owners(json.dumps(wire).encode(), monkeypatch)


@pytest.mark.parametrize(
    "fault",
    [
        "runtime-validator",
        "strategy-validator",
        "runtime-declaration",
        "instance-validator",
        "validator-return",
    ],
)
def test_missing_or_invalid_owner_preflight_contract_rejects_before_mutation(
    wire, fault, monkeypatch
):
    if fault == "runtime-validator":
        monkeypatch.setattr(CheckedRuntime, "validate_resume_state", None)
    elif fault == "strategy-validator":
        monkeypatch.setattr(CheckedStrategy, "validate_resume_state", None)
    elif fault == "runtime-declaration":
        monkeypatch.setattr(CheckedStrategy, "realtime_resume_runtime", "hidden")
    elif fault == "instance-validator":
        monkeypatch.setattr(
            CheckedRuntime, "validate_resume_state", lambda self, state, **kwargs: None
        )
    else:
        monkeypatch.setattr(
            CheckedRuntime, "validate_resume_state", staticmethod(lambda *args, **kwargs: False)
        )
    reject_preserving_live_owners(json.dumps(wire).encode(), monkeypatch)


def test_classmethod_owner_preflight_is_supported(monkeypatch):
    runtime_validate = CheckedRuntime.validate_resume_state
    strategy_validate = CheckedStrategy.validate_resume_state

    def runtime_validator(cls, state, **context):
        assert cls is CheckedRuntime
        return runtime_validate(state, **context)

    def strategy_validator(cls, state, **context):
        assert cls is CheckedStrategy
        return strategy_validate(state, **context)

    payload = JsonResumeStateSerializer().dumps(export_cut())
    monkeypatch.setattr(CheckedRuntime, "validate_resume_state", classmethod(runtime_validator))
    monkeypatch.setattr(CheckedStrategy, "validate_resume_state", classmethod(strategy_validator))
    config, bars = scenario()
    engine = BacktestEngine(config)
    assert engine.run(CheckedStrategy, bars=bars, resume_state=payload).status == "completed"
    assert observed(engine) == FULL


def test_late_owner_failure_receives_detached_state_and_preserves_live_runtime(wire, monkeypatch):
    from backtest_engine.core.resume_realtime import admit_realtime_resume
    from backtest_engine.core.state_snapshot import clone_state
    from backtest_engine.models import BarSeries

    state = JsonResumeStateSerializer().loads(json.dumps(wire).encode())
    before = clone_state(state)
    config, bars = scenario()
    runtime_before = config.runtime.export_state()

    def fail_late(candidate, **context):
        del context
        candidate["varip"]["submitted:0"] = False
        raise ValueError("late nested owner corruption")

    monkeypatch.setattr(CheckedRuntime, "validate_resume_state", staticmethod(fail_late))
    with pytest.raises(ResumeUnsupportedError, match="late nested"):
        admit_realtime_resume(state, config, CheckedStrategy, BarSeries.from_bars(bars))
    assert state == before and config.runtime.export_state() == runtime_before
    reject_preserving_live_owners(json.dumps(wire).encode(), monkeypatch)


@pytest.mark.parametrize("fault", ["provider", "generator", "diagnostic"])
def test_tick_source_and_policy_fail_closed_before_external_provider_or_owner_calls(
    wire, fault, monkeypatch
):
    from backtest_engine.core.resume_realtime import admit_realtime_resume
    from backtest_engine.models import BarSeries

    config, bars = scenario()
    state = JsonResumeStateSerializer().loads(json.dumps(wire).encode())

    def forbidden(*args, **kwargs):
        raise AssertionError("unsupported mode reached an owner/provider")

    if fault == "provider":

        class Provider:
            get_ticks = forbidden

        config.realtime_tick_provider = Provider()
    elif fault == "generator":
        config.realtime_ticks = iter(config.realtime_ticks)
    else:
        config.resume_validation_policy = "diagnostic"
    monkeypatch.setattr(CheckedRuntime, "validate_resume_state", staticmethod(forbidden))
    with pytest.raises(ResumeUnsupportedError):
        admit_realtime_resume(state, config, CheckedStrategy, BarSeries.from_bars(bars))


@pytest.mark.parametrize("active", ["_realtime_tick_execution", "_realtime_script_runtime"])
def test_exporter_does_not_stamp_committed_marker_during_an_active_attempt(active):
    from backtest_engine.models import BarSeries

    config, bars = scenario(cut=True)
    engine = BacktestEngine(config)
    assert engine.run(CheckedStrategy, bars=bars).status == "completed"
    setattr(engine, active, True)
    state = engine._export_resume_state(1, runtime=config.runtime, series=BarSeries.from_bars(bars))
    assert "realtime_resume_boundary" not in state.metadata
    with pytest.raises(ResumeUnsupportedError, match="committed parent-bar"):
        JsonResumeStateSerializer().dumps(state)


@pytest.mark.parametrize("finality", [None, Finality.OPEN, Finality.FINAL])
def test_static_bar_and_finality_transport_preserves_exact_types(finality):
    from tests.unit.test_typed_json_resume import checkpoint

    state = replace(checkpoint(), runtime_state={"bar": Bar(1, -1, 2, -3, 0, None, 2, finality)})
    codec = JsonResumeStateSerializer()
    restored = codec.loads(codec.dumps(state))
    bar = restored.runtime_state["bar"]
    assert type(bar) is Bar and bar == state.runtime_state["bar"]
    assert bar.finality is finality


if __name__ == "__main__":
    process_probe(sys.argv[1], Path(sys.argv[2]))
