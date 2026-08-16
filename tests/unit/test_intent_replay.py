from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.core.intent_replay import IntentReplayError, apply_intents_for_bar
import pytest


class LiveEntry:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 2:
            self.ctx.entry("L", "long", qty=1.0)


class ReplayEntry:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx
        self.tape = params["tape"]

    def _process_bar(self, bar, bar_index):
        apply_intents_for_bar(self.ctx, self.tape, bar_index)


def _bars():
    return [
        Bar(time=1_000 + i, open=10.0 + i, high=11.0 + i, low=9.0 + i, close=10.5 + i)
        for i in range(6)
    ]


def _cfg(**kw):
    return BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=1_000,
        end_time=1_005,
        commission_type="none",
        initial_capital=10_000,
        score_start_time=1_000,
        score_end_time=1_005,
    )


def test_tape_replay_matches_live_score_ledger_hash() -> None:
    live = BacktestEngine(_cfg()).run(LiveEntry, bars=_bars())
    tape = [
        {
            "kind": "entry",
            "bar_index": 2,
            "qty": "1",
            "idempotency_key": "run:s:entry:L:2",
            "origin_command_kind": "entry.long",
        }
    ]
    replayed = BacktestEngine(_cfg()).run(ReplayEntry, {"tape": tape}, bars=_bars())
    assert live.score_ledger_hash
    assert replayed.score_ledger_hash == live.score_ledger_hash


def test_entry_without_direction_is_fail_closed() -> None:
    ctx = type("C", (), {"entry": lambda *a, **k: None})()
    with pytest.raises(IntentReplayError, match="direction"):
        apply_intents_for_bar(
            ctx,
            [{"kind": "entry", "bar_index": 0, "idempotency_key": "r:s:entry:L:0"}],
            0,
        )


def test_hand_built_dict_is_not_a_live_pinelib_tape() -> None:
    from backtest_engine.core.intent_replay import require_live_tape

    with pytest.raises(IntentReplayError, match="live pinelib tape"):
        require_live_tape(
            [
                {
                    "kind": "entry",
                    "bar_index": 2,
                    "qty": "1",
                    "idempotency_key": "run:s:entry:L:2",
                    "origin_command_kind": "entry.long",
                }
            ]
        )


def test_replay_live_pinelib_tape_matches_score_ledger_hash() -> None:
    from pinelib.strategy.context import StrategyContext

    from backtest_engine.core.intent_replay import apply_live_intents_for_bar

    pinelib_ctx = StrategyContext(intent_run_id="run", intent_strategy_id="s")
    pinelib_ctx._runtime = type(
        "RT",
        (),
        {"bar_index": 2, "current_bar": type("B", (), {"time": 1_002})()},
    )()
    pinelib_ctx.entry("L", "long", qty=1.0)
    events = list(pinelib_ctx.intent_tape.events)
    assert events[0]["schema_id"] == "openpine.intent.v2"
    assert events[0]["content_hash"]
    assert events[0]["bar_index"] == 2
    assert events[0]["qty"] == "1"

    class ReplayLive:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx
            self.tape = params["tape"]

        def _process_bar(self, bar, bar_index):
            apply_live_intents_for_bar(self.ctx, self.tape, bar_index)

    live = BacktestEngine(_cfg()).run(LiveEntry, bars=_bars())
    replayed = BacktestEngine(_cfg()).run(ReplayLive, {"tape": events}, bars=_bars())
    assert live.score_ledger_hash
    assert replayed.score_ledger_hash == live.score_ledger_hash
