from __future__ import annotations

import pytest

from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.context import StrategyContext
from backtest_engine.core.state_snapshot import RealtimeBrokerSnapshot
from backtest_engine.errors import ResumeUnsupportedError
from backtest_engine.models import Diagnostic, Fill, Order, Position


def _engine() -> BacktestEngine:
    return BacktestEngine(
        BacktestConfig(
            symbol="TEST",
            timeframe="1",
            start_time=0,
            end_time=10,
            commission_type="none",
        )
    )


def _order(order_id: str = "L") -> Order:
    return Order(
        id=order_id,
        kind="entry",
        direction="long",
        side="buy",
        position_effect="open",
        order_type="market",
        qty=1.0,
        created_bar_index=0,
        created_time=0,
        active_from_bar_index=1,
    )


def _fill(order_id: str = "L") -> Fill:
    return Fill(
        order_id=order_id,
        bar_index=1,
        time=1,
        price=100.0,
        qty=1.0,
        direction="long",
        side="buy",
        position_effect="open",
        position_direction_before="flat",
        position_direction_after="long",
        reason="test",
        commission=0.0,
        slippage_value=0.0,
    )


def test_realtime_broker_snapshot_restores_cash_position_orders_and_diagnostics() -> (
    None
):
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    engine.cash = 9000.0
    engine.equity = 9100.0
    engine.peak_equity = 9200.0
    engine.max_drawdown = 100.0
    engine.max_drawdown_percent = 1.1
    engine.position = Position(
        size=1.0, avg_price=100.0, direction="long", open_profit=100.0
    )
    engine.orders = [_order()]
    engine.fills = [_fill()]
    engine.events = [Diagnostic("BEFORE", "before", "info")]
    engine.last_trade_bar = 1
    checkpoint = engine._export_realtime_broker_state()

    engine.cash = 8000.0
    engine.position.size = 5.0
    engine.orders[0].qty = 9.0
    engine.fills.append(_fill("X"))
    engine.events.append(Diagnostic("AFTER", "after", "info"))

    engine._restore_realtime_broker_state(checkpoint, ctx)

    assert engine.cash == 9000.0
    assert engine.equity == 9100.0
    assert engine.peak_equity == 9200.0
    assert engine.max_drawdown == 100.0
    assert engine.position.size == 1.0
    assert engine.orders[0].qty == 1.0
    assert [f.order_id for f in engine.fills] == ["L"]
    assert [e.code for e in engine.events] == ["BEFORE"]
    assert engine.last_trade_bar == 1
    assert ctx.state is engine.state
    assert engine.state.position_size == 1.0
    assert engine.state._open_trades_ref is engine.open_trades


def test_realtime_broker_snapshot_is_detached_from_later_mutations() -> None:
    engine = _engine()
    engine.position = Position(size=1.0, avg_price=100.0, direction="long")
    engine.orders = [_order()]
    checkpoint = engine._export_realtime_broker_state()

    engine.position.size = 7.0
    engine.orders[0].qty = 7.0

    assert checkpoint.position.size == 1.0
    assert checkpoint.orders[0].qty == 1.0


def test_realtime_broker_restore_rejects_resume_snapshot_type() -> None:
    engine = _engine()
    with pytest.raises(ResumeUnsupportedError, match="RealtimeBrokerSnapshot"):
        engine._restore_realtime_broker_state(object())  # type: ignore[arg-type]


def test_realtime_broker_snapshot_type_is_exported() -> None:
    engine = _engine()
    assert isinstance(engine._export_realtime_broker_state(), RealtimeBrokerSnapshot)
