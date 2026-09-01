from __future__ import annotations

import pinelib
from pinelib import CallbackFrame, RuntimeLanguageContext, RuntimeSession
from pinelib.events import SourceSpan

from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.core.delegated_strategy_intents import (
    DELEGATION_SCHEMA_ID,
    ENTRY_CAPABILITY_ID,
    OWNER,
    DelegatedStrategyIntentHandler,
    build_delegated_strategy_dispatcher,
)
from backtest_engine.core.intent_replay import IntentReplayIdentity

BARS = [
    Bar(1, 10, 10, 10, 10, time_close=2),
    Bar(2, 12, 12, 12, 12, time_close=3),
    Bar(3, 15, 15, 15, 15, time_close=4),
    Bar(4, 15, 15, 15, 15, time_close=5),
]


class MarketEntryCloseAll:
    def __init__(self, params, runtime, ctx):
        del params, runtime
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        del bar
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=2)
        if bar_index == 2:
            self.ctx.close_all()


def test_backtest_engine_is_the_authoritative_fill_and_trade_ledger() -> None:
    result = BacktestEngine(
        BacktestConfig(
            symbol="S",
            timeframe="1D",
            start_time=1,
            end_time=10,
            initial_capital=100_000.0,
            commission_type="percent",
            commission_value=0.0,
        finality_policy="ALLOW_OPEN",
         )
    ).run(MarketEntryCloseAll, bars=BARS)

    assert result.closed_trades is not None
    assert len(result.closed_trades) == 1
    trade = result.closed_trades[0]
    assert trade.qty == 2
    assert trade.entry_price == 12
    assert trade.exit_price == 15
    assert result.net_profit == 6
    assert result.final_equity == 100_006


def test_pinelib_records_delegated_intents_without_creating_a_fill_ledger() -> None:
    handler = DelegatedStrategyIntentHandler(
        identity=IntentReplayIdentity(
            run_id="run-ledger-boundary",
            strategy_id="strategy-ledger-boundary",
            stack_id="sha256:" + "a" * 64,
            semantic_profile="strict_5x",
            series_id="series-ledger-boundary",
            instrument_id="S",
            timeframe="1D",
        ),
        producer_commit="b" * 40,
        bar_open_time_utc_ms={0: 1},
    )
    runtime = RuntimeSession(
        RuntimeLanguageContext(
            6,
            "2026-09-01",
            "pine-v6",
            "sha256:" + "c" * 64,
            "compiler_annotation",
        ),
        delegated_dispatcher=build_delegated_strategy_dispatcher(handler),
    )
    transaction = runtime.begin(CallbackFrame("HISTORICAL_EVAL", 0, bar_index=0))
    direction = transaction.resolve_delegated_value(
        owner=OWNER,
        schema_id=DELEGATION_SCHEMA_ID,
        capability_id="strategy.long",
    )
    transaction.dispatch_delegated(
        owner=OWNER,
        schema_id=DELEGATION_SCHEMA_ID,
        capability_id=ENTRY_CAPABILITY_ID,
        symbol_id="pine:function:strategy.entry",
        overload_id="pine:function:strategy.entry#canonical",
        arguments={"positional": ["L", direction], "named": {"qty": 2}},
        call_site_id="main.pine:1:1",
        source_span=SourceSpan(
            "sha256:" + "d" * 64,
            "main.pine",
            1,
            1,
            1,
            40,
        ),
    )

    committed = transaction.commit()
    intents = handler.seal_committed(
        [output.value for output in committed.delegated_outputs]
    )

    assert len(intents) == 1
    assert intents[0]["kind"] == "entry"
    assert intents[0]["order_id"] == "L"
    assert intents[0]["qty"] == "2"
    assert {
        "fill_price",
        "trade_id",
        "position_size",
        "realized_pnl",
        "equity",
    }.isdisjoint(intents[0])
