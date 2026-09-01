from __future__ import annotations

from backtest_engine import BacktestConfig
from backtest_engine.core.delegated_strategy_intents import (
    DELEGATION_SCHEMA_ID,
    OWNER,
    DelegatedStrategyIntentHandler,
    build_delegated_strategy_dispatcher,
)
from backtest_engine.core.intent_replay import IntentReplayIdentity


def test_rc6_generated_strategy_boundary_is_delegated_and_immutable() -> None:
    identity = IntentReplayIdentity(
        run_id="run",
        strategy_id="strategy",
        series_id="series",
        instrument_id="instrument",
        timeframe="1m",
        semantic_profile="strict_5x",
        stack_id="sha256:" + "a" * 64,
    )
    handler = DelegatedStrategyIntentHandler(
        identity=identity,
        producer_commit="c" * 40,
        bar_open_time_utc_ms={0: 1_000},
        config=BacktestConfig(symbol="S", timeframe="1m", start_time=0, end_time=0),
    )
    dispatcher = build_delegated_strategy_dispatcher(handler)

    assert dispatcher.resolve_value(
        OWNER, DELEGATION_SCHEMA_ID, "strategy.long"
    ) == "strategy.long"
