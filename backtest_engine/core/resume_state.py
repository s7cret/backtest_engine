"""Resume-state export and restore orchestration for BacktestEngine."""

from __future__ import annotations

from typing import Any

from backtest_engine.context import StrategyContext, StrategyStateView
from backtest_engine.core.state_snapshot import BrokerSnapshot, build_resume_state
from backtest_engine.errors import ResumeUnsupportedError
from backtest_engine.models import BacktestResumeState


def restore_resume_state(
    engine: Any,
    resume_state: BacktestResumeState,
    strategy: Any,
    runtime: Any,
    ctx: StrategyContext,
) -> int:
    if resume_state.broker_state is None:
        raise ResumeUnsupportedError(
            "resume_state is missing broker_state; use BacktestEngine export_resume_state or provide a compatible snapshot"
        )
    expected_hash = engine._config_hash()
    if resume_state.config_snapshot_hash != expected_hash:
        msg = "resume state config hash does not match current config snapshot"
        if engine.config.resume_validation_policy == "strict":
            raise ResumeUnsupportedError(msg)
        engine._diag("RESUME_CONFIG_MISMATCH", msg, "warning")
    broker = resume_state.broker_state
    if not isinstance(broker, BrokerSnapshot):
        raise ResumeUnsupportedError(
            "resume_state.broker_state must be a BrokerSnapshot from core.state_snapshot"
        )
    engine.cash = broker.cash
    engine.equity = broker.equity
    engine.peak_equity = broker.peak_equity
    engine.max_drawdown = broker.max_drawdown
    engine.max_drawdown_percent = broker.max_drawdown_percent
    engine.trough_equity = broker.trough_equity
    engine.max_runup = broker.max_runup
    engine.max_runup_percent = broker.max_runup_percent
    engine.position = broker.position
    engine.orders = broker.orders
    engine.fills = broker.fills
    engine.closed_trades = broker.closed_trades
    engine.open_trades = broker.open_trades
    engine.last_trade_bar = broker.last_trade_bar
    engine.state = StrategyStateView(
        initial_capital=engine.config.initial_capital,
        cash=engine.cash,
        equity=engine.equity,
        _open_trades_ref=engine.open_trades,
        _closed_trades_ref=engine.closed_trades,
    )
    ctx.state = engine.state
    engine._update_state()
    if resume_state.runtime_state is not None:
        restore = getattr(runtime, "restore_state", None)
        if not callable(restore):
            raise ResumeUnsupportedError(
                "runtime_state is present but runtime does not implement restore_state(state)"
            )
        restore(resume_state.runtime_state)
    if resume_state.strategy_state is not None:
        restore = getattr(strategy, "restore_state", None)
        if not callable(restore):
            raise ResumeUnsupportedError(
                "strategy_state is present but strategy does not implement restore_state(state)"
            )
        restore(resume_state.strategy_state)
    return max(0, resume_state.bar_index + 1)


def export_resume_state(
    engine: Any,
    bar_index: int,
    strategy: Any | None = None,
    runtime: Any | None = None,
) -> BacktestResumeState:
    strategy_export = getattr(strategy, "export_state", None) if strategy is not None else None
    runtime_export = getattr(runtime, "export_state", None) if runtime is not None else None
    strategy_state = strategy_export() if callable(strategy_export) else None
    runtime_state = runtime_export() if callable(runtime_export) else None
    if strategy is not None and strategy_state is None:
        engine._diag(
            "RESUME_STRATEGY_STATE_UNAVAILABLE",
            "strategy does not implement export_state(); resume snapshot contains engine/runtime state only",
            "warning",
        )
    broker = BrokerSnapshot(
        engine.cash,
        engine.equity,
        engine.peak_equity,
        engine.max_drawdown,
        engine.max_drawdown_percent,
        engine.trough_equity,
        engine.max_runup,
        engine.max_runup_percent,
        engine.position,
        engine.orders,
        engine.fills,
        engine.closed_trades,
        engine.open_trades,
        engine.last_trade_bar,
    )
    return build_resume_state(
        bar_index=bar_index,
        config_snapshot_hash=engine._config_hash(),
        broker_state=broker,
        strategy_state=strategy_state,
        runtime_state=runtime_state,
        metadata={"resume_contract": "engine-broker-snapshot-v1"},
    )
