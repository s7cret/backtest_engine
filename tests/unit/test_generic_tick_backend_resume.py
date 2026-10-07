"""Generic caller-selected tick owners reuse typed native admission and effects."""

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from backtest_engine import BacktestCallbacks, BacktestEngine, JsonResumeStateSerializer
from backtest_engine.errors import ResumeUnsupportedError
from backtest_engine.execution_backends.base import PreparedNativeExecution
from tests.unit.test_typed_json_realtime_resume import (
    CUT,
    FULL,
    CheckedStrategy,
    export_cut,
    observed,
    scenario,
)


class CallerSelectedKernel:
    """Trusted backend selection token, never selected from checkpoint JSON."""


class TickBackend:
    name = "checked-tick-owner"

    def prepare_native_execution(self, **context):
        if context["strategy_class"] is not CallerSelectedKernel:
            raise ValueError("caller-selected kernel differs")
        return PreparedNativeExecution(CheckedStrategy, {}, context["callbacks"])


def test_generic_tick_public_cut_resume_and_control_use_literal_effects():
    config, bars = scenario(cut=True)
    producer = BacktestEngine(config)
    result = producer.run(CallerSelectedKernel, bars=bars, execution_backend=TickBackend())
    assert observed(producer) == CUT
    wire = JsonResumeStateSerializer().dumps(result.resume_state)
    config, bars = scenario()
    receiver = BacktestEngine(config)
    resumed = receiver.run(
        CallerSelectedKernel, bars=bars, resume_state=wire, execution_backend=TickBackend()
    )
    assert resumed.status == "completed" and observed(receiver) == FULL
    assert resumed.performance["execution_backend"] == "checked-tick-owner"
    config, bars = scenario()
    control = BacktestEngine(config)
    control.run(CheckedStrategy, bars=bars)
    assert observed(receiver) == observed(control)


@pytest.mark.parametrize(
    "fault", ["runtime-late", "strategy-late", "boundary", "runtime-bar", "schedule"]
)
def test_generic_tick_corruption_preserves_live_receiver_before_callbacks(fault, monkeypatch):
    state = export_cut()
    if fault == "runtime-late":
        state.runtime_state["varip"]["foreign-late-field"] = True
    elif fault == "strategy-late":
        state.strategy_state["ordinary"] = True
    elif fault == "runtime-bar":
        state.runtime_state["current_bar"] = replace(state.runtime_state["current_bar"], time=161)
    wire = JsonResumeStateSerializer().dumps(state)
    if fault == "boundary":
        assert b"committed-parent-bar-v1" in wire
        wire = wire.replace(b"committed-parent-bar-v1", b"provisional")
    config, bars = scenario()
    receiver = BacktestEngine(config)
    receiver.run(CheckedStrategy, bars=bars)
    if fault == "schedule":
        config.realtime_ticks[0] = replace(
            config.realtime_ticks[0], time=config.realtime_ticks[0].time + 1
        )
    before = observed(receiver)
    runtime_before = config.runtime.export_state(include_varip=True)
    ids = (receiver.position, receiver.orders, receiver.fills, receiver.callbacks, config.runtime)
    calls = []
    monkeypatch.setattr(
        receiver, "_reset_state", lambda: pytest.fail("tick negative reached reset")
    )
    monkeypatch.setattr(
        CheckedStrategy, "__init__", lambda *a, **kw: pytest.fail("tick negative constructed")
    )
    with pytest.raises(ResumeUnsupportedError):
        receiver.run(
            CallerSelectedKernel,
            bars=bars,
            resume_state=wire,
            callbacks=BacktestCallbacks(on_bar_start=lambda *a: calls.append(a)),
            execution_backend=TickBackend(),
        )
    assert config.runtime.export_state(include_varip=True) == runtime_before
    assert all(
        a is b
        for a, b in zip(
            ids,
            (
                receiver.position,
                receiver.orders,
                receiver.fills,
                receiver.callbacks,
                config.runtime,
            ),
        )
    )
    assert observed(receiver) == before and calls == []


@pytest.mark.parametrize("fault", ["provider", "schedule", "owner"])
def test_fresh_tick_backend_completes_input_and_owner_admission_before_reset(fault, monkeypatch):
    config, bars = scenario()
    backend = TickBackend()
    if fault == "provider":
        config.realtime_ticks = None
        config.realtime_tick_provider = object()
    elif fault == "schedule":
        config.realtime_ticks = []
    else:

        class MissingPreflight(CheckedStrategy):
            validate_resume_state = None

        backend.prepare_native_execution = lambda **context: PreparedNativeExecution(
            MissingPreflight, {}
        )
    receiver = BacktestEngine(config)
    runtime_before = config.runtime.export_state(include_varip=True)
    monkeypatch.setattr(receiver, "_reset_state", lambda: pytest.fail("fresh invalid tick reset"))
    with pytest.raises(ResumeUnsupportedError):
        receiver.run(CallerSelectedKernel, bars=bars, execution_backend=backend)
    assert config.runtime.export_state(include_varip=True) == runtime_before


def process(mode, directory):
    folder = Path(directory)
    started = datetime.now(timezone.utc).isoformat()
    config, bars = scenario(cut=mode == "producer")
    engine = BacktestEngine(config)
    result = engine.run(
        CallerSelectedKernel,
        bars=bars,
        resume_state=None if mode == "producer" else (folder / "checkpoint.json").read_bytes(),
        execution_backend=TickBackend(),
    )
    expected = CUT if mode == "producer" else FULL
    assert observed(engine) == expected
    if mode == "producer":
        (folder / "checkpoint.json").write_bytes(
            JsonResumeStateSerializer().dumps(result.resume_state)
        )
    report = {
        "pid": os.getpid(),
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "public_api": "BacktestEngine.run(execution_backend=TickBackend,resume_state=bytes)",
        "effects": observed(engine),
        "independent_literal_oracle": True,
    }
    (folder / (mode + ".json")).write_text(json.dumps(report, indent=2) + "\n")


def test_generic_tick_exporter_exits_before_independent_consumer(tmp_path):
    reports = []
    for mode in ("producer", "consumer"):
        result = subprocess.run(  # noqa: S603 -- fixed interpreter and local module, output directory only
            [sys.executable, "-B", __file__, mode, str(tmp_path)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stdout + result.stderr
        reports.append(json.loads((tmp_path / (mode + ".json")).read_text()))
    assert reports[0]["pid"] != reports[1]["pid"]
    assert reports[0]["completed_utc"] < reports[1]["started_utc"]


if __name__ == "__main__":
    process(sys.argv[1], sys.argv[2])
