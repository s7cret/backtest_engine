from __future__ import annotations

from copy import deepcopy

import pytest

from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.context import StrategyContext
from backtest_engine.core.realtime import (
    BarTickSlice,
    RealtimeOrderFillOracleStatus,
    RealtimeTickCommitPolicy,
)
from backtest_engine.errors import ConfigError
from backtest_engine.models import Bar, Fill, Order, Position, Tick, Trade


class _Runtime:
    def __init__(self) -> None:
        self.seen: list[float] = []

    def export_state(self, *, include_varip: bool = True) -> dict[str, object]:
        return {"seen": list(self.seen), "include_varip": include_varip}

    def restore_state(self, state: object) -> None:
        assert isinstance(state, dict)
        self.seen = list(state["seen"])

    def update_realtime_tick(self, tick: object) -> Bar:
        price = float(getattr(tick, "price"))
        self.seen.append(price)
        return Bar(time=int(getattr(tick, "time") or 0), open=10, high=price, low=10, close=price, volume=1)


class _BaseStrategy:
    def __init__(self, ctx: StrategyContext) -> None:
        self.ctx = ctx
        self.seen: list[float] = []

    def export_state(self) -> dict[str, object]:
        return {"seen": list(self.seen)}

    def restore_state(self, state: object) -> None:
        assert isinstance(state, dict)
        self.seen = list(state["seen"])


class _EveryTickMarketEntry(_BaseStrategy):
    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        self.seen.append(bar.close)
        self.ctx.entry(f"L{bar_index}_{len(self.seen)}", "long", qty=1)


class _EveryTickBracketExit(_BaseStrategy):
    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        self.seen.append(bar.close)
        self.ctx.exit(
            f"XL{bar_index}_{len(self.seen)}",
            from_entry="PRE",
            qty=1,
            limit=bar.close + 1,
            stop=bar.close - 1,
            oca_name="bracket",
            oca_type="cancel",
        )


class _EveryTickOcaCommands(_BaseStrategy):
    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        self.seen.append(bar.close)
        self.ctx.entry(f"A{bar_index}", "long", qty=1, oca_name="grp", oca_type="cancel")
        self.ctx.order(f"B{bar_index}", "short", qty=1, limit=bar.close + 2, oca_name="grp", oca_type="cancel")


def _engine() -> BacktestEngine:
    return BacktestEngine(
        BacktestConfig(symbol="TEST", timeframe="1", start_time=0, end_time=999, commission_type="none")
    )


def _slice() -> BarTickSlice:
    return BarTickSlice(
        bar_index=5,
        bar=Bar(time=100, open=10, high=12, low=9, close=10, time_close=160),
        ticks=(Tick(110, 10.5, volume=1), Tick(120, 11.0, volume=1), Tick(150, 9.5, volume=1)),
    )


def _seed_broker(engine: BacktestEngine) -> None:
    engine.cash = 8765.0
    engine.equity = 8812.0
    engine.position = Position(size=1.0, avg_price=10.0, direction="long")
    engine.orders = [
        Order("PRE", "entry", "long", "buy", "open", "market", 1.0, 4, 90, 5, status="active")
    ]
    engine.fills = [Fill("PRE", 4, 90, 10.0, 1.0, "long", "buy", "open", "flat", "long", "open", 0.0, 0.0)]
    engine.open_trades = [
        Trade("PRE", "PRE", None, "long", 90, 4, 10.0, None, None, None, 1.0, 0.0, 0.0, 0.0, 0.0, is_open=True)
    ]
    engine.closed_trades = [
        Trade("OLD", "OLD", "X", "long", 10, 1, 8.0, 20, 2, 9.0, 1.0, 0.0, 0.0, 1.0, 12.5, is_open=False)
    ]


def test_final_tick_rejected_order_restores_preexisting_command_buffer() -> None:
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    ctx.entry("PRE_BUFFER", "long", qty=2)
    strategy = _EveryTickMarketEntry(ctx)

    with pytest.raises(ConfigError, match="order commands require TradingView intrabar order/fill oracle"):
        engine._guarded_realtime_strategy_tick_loop_skeleton(
            _slice(),
            ctx=ctx,
            strategy=strategy,
            runtime=_Runtime(),
            commit_policy=RealtimeTickCommitPolicy(commit_final_tick=True),
        )

    assert [(cmd.name, cmd.kwargs["id"]) for cmd in ctx.buffer.commands] == [("entry", "PRE_BUFFER")]
    assert strategy.seen == []


def test_rejected_final_tick_order_leaves_broker_orders_fills_and_trades_unchanged() -> None:
    engine = _engine()
    _seed_broker(engine)
    before = deepcopy((engine.orders, engine.fills, engine.open_trades, engine.closed_trades, engine.position, engine.cash, engine.equity))
    ctx = StrategyContext(engine.config, engine.state)
    strategy = _EveryTickOcaCommands(ctx)

    with pytest.raises(ConfigError):
        engine._guarded_realtime_strategy_tick_loop_skeleton(
            _slice(),
            ctx=ctx,
            strategy=strategy,
            runtime=_Runtime(),
            commit_policy=RealtimeTickCommitPolicy(commit_final_tick=True),
        )

    assert (engine.orders, engine.fills, engine.open_trades, engine.closed_trades, engine.position, engine.cash, engine.equity) == before
    assert ctx.buffer.commands == []


def test_realtime_broker_snapshot_is_deep_copied_for_rejected_final_commit() -> None:
    engine = _engine()
    _seed_broker(engine)
    before_orders = deepcopy(engine.orders)
    before_fills = deepcopy(engine.fills)
    before_open = deepcopy(engine.open_trades)
    before_closed = deepcopy(engine.closed_trades)
    ctx = StrategyContext(engine.config, engine.state)

    with pytest.raises(ConfigError):
        engine._guarded_realtime_strategy_tick_loop_skeleton(
            _slice(),
            ctx=ctx,
            strategy=_EveryTickMarketEntry(ctx),
            runtime=_Runtime(),
            commit_policy=RealtimeTickCommitPolicy(commit_final_tick=True),
        )

    assert engine.orders == before_orders and engine.orders is not before_orders
    assert engine.fills == before_fills and engine.fills is not before_fills
    assert engine.open_trades == before_open and engine.open_trades is not before_open
    assert engine.closed_trades == before_closed and engine.closed_trades is not before_closed


def test_discarded_every_tick_market_entries_do_not_leak_commands() -> None:
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    strategy = _EveryTickMarketEntry(ctx)
    runtime = _Runtime()

    attempts = engine._guarded_realtime_strategy_tick_loop_skeleton(_slice(), ctx=ctx, strategy=strategy, runtime=runtime)

    assert len(attempts) == 3
    assert all(attempt.rolled_back and not attempt.committed for attempt in attempts)
    assert ctx.buffer.commands == []
    assert strategy.seen == []
    assert runtime.seen == []


def test_discarded_every_tick_limit_stop_exits_do_not_leak_commands() -> None:
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    strategy = _EveryTickBracketExit(ctx)

    engine._guarded_realtime_strategy_tick_loop_skeleton(_slice(), ctx=ctx, strategy=strategy, runtime=_Runtime())

    assert ctx.buffer.commands == []
    assert strategy.seen == []


def test_oca_bracket_like_commands_fail_closed_on_final_commit() -> None:
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    strategy = _EveryTickOcaCommands(ctx)

    with pytest.raises(ConfigError, match="order commands require TradingView intrabar order/fill oracle"):
        engine._guarded_realtime_strategy_tick_loop_skeleton(
            _slice(),
            ctx=ctx,
            strategy=strategy,
            runtime=_Runtime(),
            commit_policy=RealtimeTickCommitPolicy(commit_final_tick=True),
        )

    assert ctx.buffer.commands == []
    assert strategy.seen == []


def test_allow_intrabar_order_fills_requires_explicit_proven_oracle_object() -> None:
    engine = _engine()
    ctx = StrategyContext(engine.config, engine.state)
    strategy = _BaseStrategy(ctx)

    with pytest.raises(ConfigError, match="explicit TradingView tick oracle proof"):
        engine._guarded_realtime_strategy_tick_loop_skeleton(
            _slice(),
            ctx=ctx,
            strategy=strategy,
            commit_policy=RealtimeTickCommitPolicy(allow_intrabar_order_fills=True),
        )

    with pytest.raises(ConfigError, match="not proven"):
        engine._guarded_realtime_strategy_tick_loop_skeleton(
            _slice(),
            ctx=ctx,
            strategy=strategy,
            commit_policy=RealtimeTickCommitPolicy(
                allow_intrabar_order_fills=True,
                intrabar_order_fill_oracle_proof=RealtimeOrderFillOracleStatus(status="blocked").as_proof(),
            ),
        )
