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
