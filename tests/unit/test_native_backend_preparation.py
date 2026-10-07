"""The generic backend contract admits complete state before native live changes."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from openpine_contracts import Finality

from backtest_engine import BacktestConfig, BacktestEngine, Bar, JsonResumeStateSerializer
from backtest_engine.errors import ResumeUnsupportedError
from backtest_engine.execution_backends.base import PreparedNativeExecution


class Strategy:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx
        self.visited = []

    def run_bar(self, bar, index):
        self.visited.append(index)
        if index == 0:
            self.ctx.entry("L", "long", qty=2)

    def export_state(self):
        return {"visited": list(self.visited)}

    def restore_state(self, state):
        self.visited = list(state["visited"])


def inputs():
    config = BacktestConfig(
        "S",
        "1m",
        0,
        120000,
        initial_capital=1000,
        mintick=1,
        commission_type="none",
        commission_value=0,
        force_close_on_end=False,
        export_resume_state=True,
        early_stop_enabled=True,
        min_equity_stop=1000,
    )
    bars = [
        Bar(0, 100, 100, 100, 100, 1, finality=Finality.FINAL),
        Bar(60000, 100, 110, 100, 110, 1, finality=Finality.FINAL),
        Bar(120000, 110, 120, 110, 120, 1, finality=Finality.FINAL),
    ]
    return config, bars


def checkpoint():
    config, bars = inputs()
    result = BacktestEngine(config).run(Strategy, bars=bars)
    assert result.resume_state.bar_index == 0
    return JsonResumeStateSerializer().dumps(result.resume_state)


def test_public_backend_preparation_precedes_reset_and_reuses_native_restore(monkeypatch):
    wire = checkpoint()
    config, bars = inputs()
    receiver = BacktestEngine(config)
    order = []
    reset = receiver._reset_state

    def observed_reset():
        order.append("reset")
        reset()

    def prepare(**context):
        order.append("prepare")
        assert context["engine"] is receiver
        assert context["resume_state"].strategy_state == {"visited": [0]}
        return PreparedNativeExecution(Strategy, {}, context["callbacks"])

    monkeypatch.setattr(receiver, "_reset_state", observed_reset)
    result = receiver.run(
        Strategy,
        bars=bars,
        resume_state=wire,
        execution_backend=SimpleNamespace(
            name="trusted-local-owner", prepare_native_execution=prepare
        ),
    )
    assert order == ["prepare", "reset"]
    assert result.performance["execution_backend"] == "trusted-local-owner"
    assert receiver.last_strategy.visited == [0, 1, 2]
    assert [(f.side, f.qty, f.price, f.bar_index) for f in receiver.fills] == [("buy", 2, 100, 1)]
    assert receiver.position.size == 2 and receiver.equity == 1040


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        object(),
        PreparedNativeExecution(None, {}),
        PreparedNativeExecution(Strategy, []),
        PreparedNativeExecution(Strategy, {1: 2}),
        PreparedNativeExecution(Strategy, {}, object()),
    ],
)
def test_incomplete_preparation_rejects_before_reset(bad, monkeypatch):
    config, bars = inputs()
    receiver = BacktestEngine(config)
    identities = (receiver.position, receiver.orders, receiver.callbacks)
    monkeypatch.setattr(
        receiver, "_reset_state", lambda: pytest.fail("incomplete preparation reset")
    )
    with pytest.raises(ResumeUnsupportedError, match="preparation contract"):
        receiver.run(
            Strategy,
            bars=bars,
            execution_backend=SimpleNamespace(
                name="trusted-local-owner",
                prepare_native_execution=lambda **context: bad,
            ),
        )
    assert all(
        a is b for a, b in zip(identities, (receiver.position, receiver.orders, receiver.callbacks))
    )


@pytest.mark.parametrize("as_bytes", [False, True])
def test_late_preparer_failure_cannot_mutate_original_checkpoint_or_receiver(as_bytes, monkeypatch):
    wire = checkpoint()
    state = JsonResumeStateSerializer().loads(wire)
    config, bars = inputs()
    receiver = BacktestEngine(config)
    receiver.run(Strategy, bars=bars)
    identities = (receiver.position, receiver.orders, receiver.fills, receiver.last_strategy)

    def fail(**context):
        context["resume_state"].strategy_state["visited"].append(999)
        raise ValueError("late detached failure")

    monkeypatch.setattr(receiver, "_reset_state", lambda: pytest.fail("late preparation reset"))
    with pytest.raises(ResumeUnsupportedError, match="late detached failure"):
        receiver.run(
            Strategy,
            bars=bars,
            resume_state=wire if as_bytes else state,
            execution_backend=SimpleNamespace(
                name="trusted-local-owner", prepare_native_execution=fail
            ),
        )
    assert state.strategy_state == {"visited": [0]}
    assert JsonResumeStateSerializer().loads(wire).strategy_state == {"visited": [0]}
    assert all(
        a is b
        for a, b in zip(
            identities, (receiver.position, receiver.orders, receiver.fills, receiver.last_strategy)
        )
    )


@pytest.mark.parametrize("fault", ["config", "prefix", "transport", "tick"])
def test_core_admission_rejects_before_owner_preparer(fault, monkeypatch):
    config, bars = inputs()
    wire = checkpoint()
    if fault == "config":
        config = replace(config, initial_capital=999)
    elif fault == "prefix":
        bars = [replace(bars[0], high=101), *bars[1:]]
    elif fault == "transport":
        wire = b'{"schema": "backtest-engine.resume", "schema": "duplicate"}'
    else:
        config = replace(config, calc_on_every_tick=True)
    receiver = BacktestEngine(config)
    calls = []

    def forbidden(**context):
        calls.append(context)
        raise AssertionError("invalid core input reached owner")

    monkeypatch.setattr(receiver, "_reset_state", lambda: pytest.fail("invalid core input reset"))
    with pytest.raises(ResumeUnsupportedError):
        receiver.run(
            Strategy,
            bars=bars,
            resume_state=wire,
            execution_backend=SimpleNamespace(
                name="trusted-local-owner", prepare_native_execution=forbidden
            ),
        )
    assert calls == []


def test_execute_only_foreign_backend_keeps_fail_closed_bytes_behavior(monkeypatch):
    config, bars = inputs()
    receiver = BacktestEngine(config)
    calls = []
    monkeypatch.setattr(receiver, "_reset_state", lambda: pytest.fail("execute-only backend reset"))
    with pytest.raises(ResumeUnsupportedError, match="foreign execution needs its owner codec"):
        receiver.run(
            Strategy,
            bars=bars,
            resume_state=checkpoint(),
            execution_backend=SimpleNamespace(
                name="execute-only", execute=lambda **context: calls.append(context)
            ),
        )
    assert calls == []


@pytest.mark.parametrize("name", [None, ""])
def test_prepared_backend_name_is_required_before_owner_call(name, monkeypatch):
    config, bars = inputs()
    receiver = BacktestEngine(config)
    calls = []
    monkeypatch.setattr(receiver, "_reset_state", lambda: pytest.fail("unnamed backend reset"))
    with pytest.raises(ResumeUnsupportedError, match="explicit name"):
        receiver.run(
            Strategy,
            bars=bars,
            execution_backend=SimpleNamespace(
                name=name, prepare_native_execution=lambda **context: calls.append(context)
            ),
        )
    assert calls == []
