from __future__ import annotations

import types
from typing import Any

import pytest

from backtest_engine import BacktestConfig
from backtest_engine.context.strategy_state_view import StrategyStateView
from backtest_engine.core import execution_backend_adapter as adapter
from backtest_engine.errors import ConfigError, StrategyRuntimeError
from backtest_engine.execution_backends.base import (
    BackendBarResult,
    BackendExecutionResult,
)
from backtest_engine.models import Trade


def test_strategy_state_view_all_accessors_and_defaults() -> None:
    open_trade = Trade(
        id="o",
        entry_id="LO",
        exit_id=None,
        direction="short",
        entry_time=1,
        entry_bar_index=2,
        entry_price=100.0,
        exit_time=None,
        exit_bar_index=None,
        exit_price=None,
        qty=3.0,
        commission_entry=1.0,
        commission_exit=2.0,
        profit=-5.0,
        profit_percent=-1.0,
        max_runup=None,
        max_drawdown=None,
    )
    closed_trade = Trade(
        id="c",
        entry_id="LC",
        exit_id="XC",
        direction="long",
        entry_time=10,
        entry_bar_index=11,
        entry_price=90.0,
        exit_time=20,
        exit_bar_index=21,
        exit_price=95.0,
        qty=2.0,
        commission_entry=0.5,
        commission_exit=0.25,
        profit=10.0,
        profit_percent=5.0,
        max_runup=12.0,
        max_drawdown=3.0,
    )
    view = StrategyStateView(
        open_trades=1,
        closed_trades=1,
        _open_trades_ref=[open_trade],
        _closed_trades_ref=[closed_trade],
    )
    assert view.open_trades_count == 1
    assert view.closed_trades_count == 1
    assert view.opentrades_entry_id(0) == "LO"
    assert view.opentrades_entry_price(0) == 100.0
    assert view.opentrades_entry_bar_index(0) == 2
    assert view.opentrades_entry_time(0) == 1
    assert view.opentrades_commission(0) == 3.0
    assert view.opentrades_size(0) == -3.0
    assert view.opentrades_qty(0) == 3.0
    assert view.opentrades_side(0) == "short"
    assert view.opentrades_profit(0) == -5.0
    assert view.opentrades_profit_percent(0) == -1.0
    assert view.opentrades_max_runup(0) == 0.0
    assert view.opentrades_max_drawdown(0) == 0.0
    assert view.closedtrades_entry_id(0) == "LC"
    assert view.closedtrades_exit_id(0) == "XC"
    assert view.closedtrades_entry_price(0) == 90.0
    assert view.closedtrades_exit_price(0) == 95.0
    assert view.closedtrades_entry_time(0) == 10
    assert view.closedtrades_exit_time(0) == 20
    assert view.closedtrades_commission(0) == 0.75
    assert view.closedtrades_size(0) == 2.0
    assert view.closedtrades_qty(0) == 2.0
    assert view.closedtrades_side(0) == "long"
    assert view.closedtrades_profit(0) == 10.0
    assert view.closedtrades_profit_percent(0) == 5.0
    assert view.closedtrades_max_runup(0) == 12.0
    assert view.closedtrades_max_drawdown(0) == 3.0
    assert view.closedtrades_entry_bar_index(0) == 11
    assert view.closedtrades_exit_bar_index(0) == 21


def test_backend_adapter_helpers_and_error_paths() -> None:
    with pytest.raises(ConfigError, match="unknown execution backend"):
        adapter.resolve_execution_backend("pine_runtime")
    with pytest.raises(ConfigError, match="unknown execution backend"):
        adapter.resolve_execution_backend("missing")
    with pytest.raises(ConfigError, match="must provide execute"):
        adapter.ensure_executable_backend(object())  # type: ignore[arg-type]

    bar_results = [
        BackendBarResult(time=1, phase="prehistory", equity=None),
        BackendBarResult(
            time=2,
            phase="score",
            equity=101.0,
            openprofit=1.0,
            netprofit=2.0,
            position_size=3.0,
            position_avg_price=10.0,
        ),
        BackendBarResult(
            time=3,
            phase="score",
            equity=99.0,
            openprofit=0.0,
            netprofit=-1.0,
            position_size=0.0,
        ),
    ]
    backend_result = BackendExecutionResult(
        bar_results=bar_results,
        diagnostics={
            "runtime_diagnostics": [
                {"code": "X", "message": "warn", "extra": 1},
                "ignored",
            ]
        },
    )
    curve, score = adapter.backend_equity_curve(backend_result, initial_capital=100.0)
    assert [p.equity for p in curve] == [101.0, 99.0]
    assert [p.time for p in score] == [2, 3]
    warnings = adapter.backend_runtime_warnings(backend_result)
    assert warnings[0].code == "X"

    trade = types.SimpleNamespace(
        entry_id="L",
        commission_entry=0.1,
        commission_exit=0.2,
        max_runup=5.0,
        max_drawdown=2.0,
        direction="long",
        entry_time=1,
        entry_bar_index=2,
        entry_price=100.0,
        exit_time=3,
        exit_bar_index=4,
        exit_price=105.0,
        qty=1.0,
        profit=5.0,
        profit_percent=5.0,
        exit_reason="take_profit",
    )
    converted = adapter.backend_trades(
        BackendExecutionResult(bar_results=[], trades=[trade])
    )[0]
    assert converted.id == "pine_0"
    assert converted.bars_held == 2
    missing = types.SimpleNamespace(
        entry_time=1, entry_bar_index=1, entry_price=1.0, qty=1.0
    )
    with pytest.raises(StrategyRuntimeError, match="commission_entry"):
        adapter.backend_trades(BackendExecutionResult(bar_results=[], trades=[missing]))


def test_apply_backend_result_mutates_engine_summary() -> None:
    class Engine:
        def __init__(self) -> None:
            self.closed_trades: list[Any] = []
            self.open_trades: list[Any] = []
            self._backend_equity_curve: list[Any] = []
            self._score_equity_points: list[Any] = []
            self.warnings: list[Any] = []
            self.equity = 0.0
            self.cash = 0.0
            self.max_drawdown = 0.0
            self.max_drawdown_percent = 0.0
            self.trough_equity = 0.0
            self.max_runup = 0.0
            self.max_runup_percent = 0.0

    engine = Engine()
    result = BackendExecutionResult(
        bar_results=[
            BackendBarResult(
                time=1, phase="score", equity=110.0, openprofit=5.0, netprofit=10.0
            )
        ],
        diagnostics={"runtime_diagnostics": [{"message": "hello"}]},
    )
    adapter.apply_backend_result(
        engine,
        result,
        BacktestConfig(symbol="BTCUSDT", timeframe="1", start_time=0, end_time=1, finality_policy="ALLOW_OPEN"),
    )
    assert engine.equity == 110.0
    assert engine.cash == 105.0
    assert len(engine._score_equity_points) == 1
    assert engine.warnings[0].code == "PINELIB_RUNTIME_DIAGNOSTIC"
