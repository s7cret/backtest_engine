"""Native explicit tick owner selection and same-tick immediate close boundaries."""

from dataclasses import replace

import pytest
from openpine_contracts import Finality

from backtest_engine import BacktestEngine, Bar, JsonResumeStateSerializer, Tick
from backtest_engine.errors import ResumeUnsupportedError
from tests.unit.test_p1_deterministic_tick_replay import SerializableTickStrategy, _config
from tests.unit.test_typed_json_realtime_resume import CheckedStrategy, scenario


class TransactionOwner:
    realtime_resume_runtime = "strategy"

    def __init__(self, params, runtime, ctx):
        assert runtime is None
        self.committed = []
        self.callbacks = 0

    def run_bar(self, bar, index):
        self.callbacks += 1

    def _commit_bar(self, index):
        self.committed.append(index)

    def export_state(self):
        return {"committed": list(self.committed), "callbacks": self.callbacks}

    def restore_state(self, state):
        self.committed = list(state["committed"])
        self.callbacks = state["callbacks"]

    @staticmethod
    def validate_resume_state(state, *, bar_index, committed_bar):
        if type(state) is not dict or set(state) != {"committed", "callbacks"}:
            raise ValueError("closed complete owner schema required")
        if type(state["committed"]) is not list or state["committed"] != list(range(bar_index + 1)):
            raise ValueError("committed owner cursor mismatch")
        if any(type(index) is not int for index in state["committed"]):
            raise ValueError("committed indices must be exact integers")
        if type(state["callbacks"]) is not int or state["callbacks"] < bar_index + 1:
            raise ValueError("callback cursor is invalid")


def owner_cut():
    config, bars = scenario(cut=True)
    config.runtime = None
    engine = BacktestEngine(config)
    return config, bars, engine.run(TransactionOwner, bars=bars).resume_state


def test_strategy_owned_tick_bytes_preserve_callbacks_and_committed_cut():
    _, _, state = owner_cut()
    config, bars = scenario()
    config.runtime = None
    receiver = BacktestEngine(config)
    result = receiver.run(
        TransactionOwner, bars=bars, resume_state=JsonResumeStateSerializer().dumps(state)
    )
    control = BacktestEngine(replace(config))
    expected = control.run(TransactionOwner, bars=bars)
    assert result.resume_state.strategy_state == expected.resume_state.strategy_state
    assert result.resume_state.strategy_state["committed"] == [0, 1, 2, 3]
    assert result.resume_state.runtime_state is None
    assert result.resume_state.broker_state == expected.resume_state.broker_state


@pytest.mark.parametrize("fault", ["owner-tag", "duplicate-runtime", "missing-owner-tag"])
def test_strategy_owned_transport_rejects_unknown_or_ambiguous_runtime(fault):
    _, _, state = owner_cut()
    if fault == "owner-tag":
        state.metadata["realtime_runtime_owner"] = "checkpoint-selected-python-class"
    elif fault == "duplicate-runtime":
        state = replace(state, runtime_state={"fake": True})
    else:
        state.metadata.pop("realtime_runtime_owner")
    with pytest.raises(ResumeUnsupportedError):
        JsonResumeStateSerializer().dumps(state)


def test_json_runtime_owner_cannot_override_caller_selected_class(monkeypatch):
    config, bars, state = owner_cut()
    receiver = BacktestEngine(config)
    before = receiver.position
    monkeypatch.setattr(receiver, "_reset_state", lambda: pytest.fail("wrong owner reached reset"))
    monkeypatch.setattr(
        CheckedStrategy, "__init__", lambda *a, **kw: pytest.fail("wrong owner constructed")
    )
    with pytest.raises(ResumeUnsupportedError, match="runtime owner differs"):
        receiver.run(
            CheckedStrategy, bars=bars, resume_state=JsonResumeStateSerializer().dumps(state)
        )
    assert receiver.position is before


@pytest.mark.parametrize("missing", ["_commit_bar", "validate_resume_state", "restore_state"])
def test_strategy_owned_tick_contract_is_admitted_before_reset(missing, monkeypatch):
    config, bars = scenario()
    config.runtime = None
    receiver = BacktestEngine(config)
    monkeypatch.setattr(TransactionOwner, missing, None)
    monkeypatch.setattr(
        receiver, "_reset_state", lambda: pytest.fail("missing owner reached reset")
    )
    with pytest.raises(ResumeUnsupportedError):
        receiver.run(TransactionOwner, bars=bars)


@pytest.mark.parametrize("process_on_close", [False, True])
def test_immediate_close_fills_nonfinal_observed_tick_without_later_tick_delay(process_on_close):
    class Strategy(SerializableTickStrategy):
        def run_bar(self, bar, index):
            if not self.runtime.varip.get("entered"):
                self.runtime.varip["entered"] = True
                self.ctx.entry("entry", "long", qty=1)
            close_bar = index > 0 if self.ctx.config.process_orders_on_close else True
            if close_bar and self.ctx.state.position_size and not self.runtime.varip.get("closed"):
                self.runtime.varip["closed"] = True
                self.ctx.close("entry", qty=1, immediately=True)

    bars = [
        Bar(0, 10, 12, 10, 12, 3, 60, Finality.FINAL),
        Bar(60, 12, 14, 12, 14, 3, 120, Finality.FINAL),
    ]
    ticks = [
        Tick(t, p, volume=1) for t, p in [(0, 10), (10, 11), (20, 12), (60, 12), (70, 13), (80, 14)]
    ]
    engine = BacktestEngine(
        _config(bars, ticks, process_orders_on_close=process_on_close, force_close_on_end=False)
    )
    engine.run(Strategy, bars=bars)
    expected = (
        [("buy", 12, 0), ("sell", 12, 1)] if process_on_close else [("buy", 11, 0), ("sell", 11, 0)]
    )
    assert [(f.side, f.price, f.bar_index) for f in engine.fills] == expected
    assert engine.position.direction == "flat" and len(engine.closed_trades) == 1
