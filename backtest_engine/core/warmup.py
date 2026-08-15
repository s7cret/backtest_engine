"""Fail-closed warmup phase machine and immutable broker reset."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from backtest_engine.errors import WarmupAdmissionError
from backtest_engine.models import Position

WarmupPolicy = Literal["CALC_ONLY", "TRADE_THROUGH_UNSCORED", "CALC_THEN_RESET_BROKER"]
ScoreEndPolicy = Literal["MARK_TO_MARKET", "FORCE_CLOSE", "LEAVE_OPEN"]
WARMUP_POLICIES: frozenset[str] = frozenset(
    {"CALC_ONLY", "TRADE_THROUGH_UNSCORED", "CALC_THEN_RESET_BROKER"}
)
SCORE_END_POLICIES: frozenset[str] = frozenset(
    {"MARK_TO_MARKET", "FORCE_CLOSE", "LEAVE_OPEN"}
)
_UNSAFE_PROJECTION_POLICIES = frozenset({"CALC_ONLY", "CALC_THEN_RESET_BROKER"})
_BROKER_READ_MARKERS = (
    "position_size",
    "open_trades",
    "closed_trades",
    "closedtrades",
    "opentrades",
    "strategy.position",
    "position.avg_price",
)


class WarmupPhase(StrEnum):
    INITIAL = "INITIAL"
    WARMUP = "WARMUP"
    SCORE = "SCORE"
    AFTER = "AFTER"
    FINALIZED = "FINALIZED"

    @property
    def label(self) -> str:
        return {
            WarmupPhase.WARMUP: "warmup",
            WarmupPhase.SCORE: "score",
            WarmupPhase.AFTER: "after",
        }.get(self, self.value.lower())


@dataclass(frozen=True, slots=True)
class BarDecision:
    phase: WarmupPhase
    intent_emission_allowed: bool
    broker_execution_allowed: bool


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    size: float = 0.0
    avg_price: float = 0.0
    direction: str = "flat"
    open_profit: float = 0.0
    realized_profit: float = 0.0


@dataclass(frozen=True, slots=True)
class BrokerState:
    cash: float
    equity: float
    peak_equity: float
    trough_equity: float
    position: PositionSnapshot
    orders: tuple[object, ...]
    fills: tuple[object, ...]
    open_trades: tuple[object, ...]
    closed_trades: tuple[object, ...]
    last_trade_bar: int | None

    @classmethod
    def canonical_initial(cls, capital: float) -> BrokerState:
        return cls(
            cash=capital,
            equity=capital,
            peak_equity=capital,
            trough_equity=capital,
            position=PositionSnapshot(),
            orders=(),
            fills=(),
            open_trades=(),
            closed_trades=(),
            last_trade_bar=None,
        )

    @classmethod
    def capture(cls, engine: Any) -> BrokerState:
        position = engine.position
        return cls(
            cash=float(engine.cash),
            equity=float(engine.equity),
            peak_equity=float(engine.peak_equity),
            trough_equity=float(engine.trough_equity),
            position=PositionSnapshot(
                size=float(position.size),
                avg_price=float(position.avg_price),
                direction=str(position.direction),
                open_profit=float(position.open_profit),
                realized_profit=float(position.realized_profit),
            ),
            orders=tuple(engine.orders),
            fills=tuple(engine.fills),
            open_trades=tuple(engine.open_trades),
            closed_trades=tuple(engine.closed_trades),
            last_trade_bar=engine.last_trade_bar,
        )

    def apply_to(self, engine: Any) -> None:
        engine.cash = self.cash
        engine.equity = self.equity
        engine.peak_equity = self.peak_equity
        engine.trough_equity = self.trough_equity
        engine.max_drawdown = 0.0
        engine.max_drawdown_percent = 0.0
        engine.max_runup = 0.0
        engine.max_runup_percent = 0.0
        engine.position = Position(
            size=self.position.size,
            avg_price=self.position.avg_price,
            direction=self.position.direction,  # type: ignore[arg-type]
            open_profit=self.position.open_profit,
            realized_profit=self.position.realized_profit,
        )
        engine.orders = list(self.orders)
        engine.fills = list(self.fills)
        engine.open_trades = list(self.open_trades)
        engine.closed_trades = list(self.closed_trades)
        engine.last_trade_bar = self.last_trade_bar
        engine._filled_exit_entry_keys = set()
        engine._closed_trade_stats_count = 0
        engine._gross_profit_total = 0.0
        engine._gross_loss_total = 0.0
        engine._win_trades_total = 0
        engine._loss_trades_total = 0
        engine._even_trades_total = 0
        engine.state.cash = engine.cash
        engine.state.equity = engine.equity
        engine.state._open_trades_ref = engine.open_trades
        engine.state._closed_trades_ref = engine.closed_trades


class WarmupPhaseMachine:
    def __init__(
        self,
        policy: str,
        *,
        warmup_end_index: int,
        score_end_index: int,
        series_len: int,
    ) -> None:
        if policy not in WARMUP_POLICIES:
            raise ValueError(f"unknown warmup_policy {policy}")
        self.policy = policy
        self.warmup_end_index = warmup_end_index
        self.score_end_index = score_end_index
        self.series_len = series_len
        self.phase = WarmupPhase.INITIAL

    def _phase_for(self, bar_index: int) -> WarmupPhase:
        if bar_index <= self.warmup_end_index:
            return WarmupPhase.WARMUP
        if bar_index <= self.score_end_index:
            return WarmupPhase.SCORE
        return WarmupPhase.AFTER

    def _decision(self, phase: WarmupPhase) -> BarDecision:
        if phase is WarmupPhase.WARMUP:
            execute = self.policy == "TRADE_THROUGH_UNSCORED"
            return BarDecision(phase, True, execute)
        if phase is WarmupPhase.SCORE:
            return BarDecision(phase, True, True)
        return BarDecision(phase, False, False)

    def begin_bar(self, bar_index: int) -> BarDecision:
        if self.phase is WarmupPhase.FINALIZED:
            return BarDecision(WarmupPhase.FINALIZED, False, False)
        self.phase = self._phase_for(bar_index)
        return self._decision(self.phase)

    def end_bar(self, bar_index: int) -> None:
        if self.phase is not WarmupPhase.FINALIZED:
            self.phase = self._phase_for(bar_index)

    def finalize(self) -> None:
        self.phase = WarmupPhase.FINALIZED


def strategy_reads_broker_projection(strategy_class: type) -> bool:
    try:
        source = inspect.getsource(strategy_class)
    except (OSError, TypeError):
        return False
    lowered = source.replace(" ", "").lower()
    return any(marker.replace(" ", "") in lowered for marker in _BROKER_READ_MARKERS)


def admit_warmup_strategy(policy: str | None, strategy_class: type) -> None:
    if policy in _UNSAFE_PROJECTION_POLICIES and strategy_reads_broker_projection(
        strategy_class
    ):
        raise WarmupAdmissionError(
            "WARMUP_UNSAFE_BROKER_PROJECTION: strategy reads broker projection "
            f"under {policy}"
        )


def discard_command_buffer(engine: Any, ctx: Any, bar_index: int, time: int) -> int:
    commands = list(ctx.buffer.commands)
    ctx.buffer.drain()
    for command in commands:
        ident = getattr(command.payload, "id", None) or command.name
        engine._event(
            "WARMUP_INTENT_DISCARDED",
            f"warmup intent {ident} discarded",
            bar_index,
            time,
            ident if isinstance(ident, str) else None,
        )
    return len(commands)


def stamp_phase(engine: Any, phase: str | None) -> None:
    if not phase:
        return
    for item in (
        *engine.orders,
        *engine.fills,
        *engine.open_trades,
        *engine.closed_trades,
    ):
        if getattr(item, "phase", None) is None:
            item.phase = phase


def apply_canonical_broker_reset(engine: Any) -> BrokerState:
    replacement = BrokerState.canonical_initial(engine.config.initial_capital)
    replacement.apply_to(engine)
    return BrokerState.capture(engine)
