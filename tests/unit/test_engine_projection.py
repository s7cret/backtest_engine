from __future__ import annotations

import json
from typing import Any

from openpine_contracts import Finality

from backtest_engine import BacktestCallbacks, BacktestConfig, BacktestEngine, Bar


class EnterWarmup:
    required_runtime_capabilities: tuple[str, ...] = ()

    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)


class EnterThenClose:
    required_runtime_capabilities: tuple[str, ...] = ()

    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1, comment="entry-comment")
        elif bar_index == 1:
            self.ctx.close("L", comment="exit-comment")


class EnterThenCloseOnSameBarFill:
    required_runtime_capabilities: tuple[str, ...] = ()

    def __init__(self, params, runtime, ctx):
        del params, runtime
        self.ctx = ctx

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        del bar
        if bar_index == 0 and self.ctx.state.position_size == 0:
            self.ctx.entry("L", "long", qty=1)
        elif bar_index == 0 and self.ctx.state.position_size > 0:
            self.ctx.close("L", immediately=True)


def _bars() -> list[Bar]:
    return [
        Bar(
            time=1_000 + index,
            open=10 + index,
            high=11 + index,
            low=9 + index,
            close=10.5 + index,
            finality=Finality.FINAL,
        )
        for index in range(4)
    ]


def _config() -> BacktestConfig:
    return BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=1_000,
        end_time=1_003,
        initial_capital=10_000,
        commission_type="none",
        score_start_time=1_002,
        score_end_time=1_003,
        warmup_policy="CALC_THEN_RESET_BROKER",
    )


def _plain_config() -> BacktestConfig:
    return BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=1_000,
        end_time=1_003,
        initial_capital=10_000,
        commission_type="none",
    )


def test_canonical_strategy_ledger_projection_has_deterministic_no_trade_state() -> None:
    engine = BacktestEngine(_plain_config())

    projection = engine.export_strategy_ledger_projection()

    assert projection == {
        "cash": 10_000.0,
        "equity": 10_000.0,
        "netprofit": 0.0,
        "openprofit": 0.0,
        "grossprofit": 0.0,
        "grossloss": 0.0,
        "position_size": 0.0,
        "position_avg_price": 0.0,
        "position_entry_name": None,
        "position_direction": "flat",
        "opentrades": 0,
        "closedtrades": 0,
        "wintrades": 0,
        "losstrades": 0,
        "eventrades": 0,
        "max_drawdown": 0.0,
        "max_runup": 0.0,
        "orders": [],
        "fills": [],
        "open_trade_log": [],
        "closed_trade_log": [],
    }
    json.dumps(projection, allow_nan=False)


def test_canonical_projection_maps_position_and_all_trade_metric_inputs() -> None:
    engine = BacktestEngine(_plain_config())
    engine.run(EnterThenClose, bars=_bars())

    projection = engine.export_strategy_ledger_projection()

    assert projection["position_size"] == 0.0
    assert projection["position_avg_price"] == 0.0
    assert projection["position_entry_name"] is None
    assert projection["opentrades"] == 0
    assert projection["closedtrades"] == 1
    assert projection["wintrades"] == 1
    assert projection["losstrades"] == 0
    assert projection["eventrades"] == 0
    assert len(projection["fills"]) == 2
    assert projection["open_trade_log"] == []

    closed_trade = projection["closed_trade_log"][0]
    assert closed_trade["entry_id"] == "L"
    assert closed_trade["exit_id"] == "L"
    assert closed_trade["entry_price"] == 11.0
    assert closed_trade["exit_price"] == 12.0
    assert closed_trade["entry_time"] == 1_001
    assert closed_trade["exit_time"] == 1_002
    assert closed_trade["entry_bar_index"] == 1
    assert closed_trade["exit_bar_index"] == 2
    assert closed_trade["profit"] == 1.0
    assert closed_trade["profit_percent"] > 0.0
    assert closed_trade["commission"] == 0.0
    assert closed_trade["qty"] == 1.0
    assert closed_trade["side"] == "long"
    assert closed_trade["size"] == 1.0
    assert closed_trade["entry_comment"] == "entry-comment"
    assert closed_trade["exit_comment"] == "exit-comment"
    assert isinstance(closed_trade["max_runup"], float)
    assert isinstance(closed_trade["max_drawdown"], float)
    json.dumps(projection, allow_nan=False)

    projection["fills"][0]["qty"] = 999
    closed_trade["entry_id"] = "forged"
    assert engine.fills[0].qty == 1.0
    assert engine.closed_trades[0].entry_id == "L"


def test_engine_exports_detached_broker_and_ledger_projections() -> None:
    engine = BacktestEngine(_config())
    engine.run(EnterWarmup, bars=_bars(), effective_pre_bars=2)

    broker = engine.export_broker_projection()
    ledger = engine.export_ledger_projection()
    assert broker["cash"] == 10_000
    assert broker["position"]["size"] == 0
    assert ledger["orders"] == []
    assert ledger["fills"] == []

    broker["position"]["size"] = 999
    ledger["orders"].append({"id": "forged"})
    assert engine.position.size == 0
    assert engine.orders == []


def test_every_strategy_callback_gets_projection_and_recalc_ordinal() -> None:
    primitives: list[dict[str, Any]] = []
    callbacks = BacktestCallbacks(on_strategy_callback=primitives.append)
    engine = BacktestEngine(_config())
    engine.run(
        EnterWarmup,
        bars=_bars(),
        effective_pre_bars=2,
        callbacks=callbacks,
    )

    assert [item["bar_index"] for item in primitives] == [0, 1, 2, 3]
    assert [item["recalc_iteration"] for item in primitives] == [0, 0, 0, 0]
    assert all(item["callback"] == "strategy" for item in primitives)
    assert all("broker" in item and "ledger" in item for item in primitives)
    assert all("projection" in item for item in primitives)


def test_close_fill_can_trigger_one_same_bar_strategy_recalculation() -> None:
    callbacks_seen: list[dict[str, Any]] = []
    config = _plain_config()
    config.calc_on_order_fills = True
    config.process_orders_on_close = True

    result = BacktestEngine(config).run(
        EnterThenCloseOnSameBarFill,
        bars=_bars(),
        callbacks=BacktestCallbacks(on_strategy_callback=callbacks_seen.append),
    )

    first_bar = [item for item in callbacks_seen if item["bar_index"] == 0]
    assert [item["recalc_iteration"] for item in first_bar] == [0, 1]
    assert [item["projection"]["position_size"] for item in first_bar] == [0.0, 1.0]
    assert result.closed_trades is not None
    assert result.closed_trades[0].entry_bar_index == 0
    assert result.closed_trades[0].exit_bar_index == 0


def test_callback_projection_is_refreshed_after_each_broker_transition() -> None:
    callbacks_seen: list[dict[str, Any]] = []
    engine = BacktestEngine(_plain_config())
    engine.run(
        EnterThenClose,
        bars=_bars(),
        callbacks=BacktestCallbacks(on_strategy_callback=callbacks_seen.append),
    )

    assert [item["projection"]["position_size"] for item in callbacks_seen] == [
        0.0,
        1.0,
        0.0,
        0.0,
    ]
    assert callbacks_seen[1]["projection"]["position_entry_name"] == "L"
    assert callbacks_seen[1]["projection"]["opentrades"] == 1
    assert callbacks_seen[1]["projection"]["open_trade_log"][0]["entry_id"] == "L"
    assert callbacks_seen[2]["projection"]["closedtrades"] == 1
    assert callbacks_seen[2]["projection"]["closed_trade_log"][0]["exit_id"] == "L"


def test_first_score_bar_callback_sees_post_reset_projection() -> None:
    primitives: list[dict[str, Any]] = []
    callbacks = BacktestCallbacks(
        extra={"on_strategy_callback": primitives.append},
    )
    engine = BacktestEngine(_config())
    engine.run(
        EnterWarmup,
        bars=_bars(),
        effective_pre_bars=2,
        callbacks=callbacks,
    )

    first_score = next(item for item in primitives if item["bar_index"] == 2)
    assert first_score["phase"] == "score"
    assert first_score["broker"]["cash"] == 10_000
    assert first_score["broker"]["equity"] == 10_000
    assert first_score["broker"]["position"]["size"] == 0
    assert first_score["ledger"]["orders"] == []
    assert first_score["ledger"]["fills"] == []
