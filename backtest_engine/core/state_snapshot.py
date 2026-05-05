from __future__ import annotations

import copy
import json
import pickle
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Protocol

from backtest_engine.models import BacktestResumeState, Diagnostic, Fill, Order, Position, Trade


class StateSerializer(Protocol):
    """Serializer extension point for resume/runtime state payloads."""

    serializer_id: str

    def dumps(self, state: object) -> bytes: ...
    def loads(self, payload: bytes) -> object: ...


class PickleStateSerializer:
    """Default same-Python-runtime serializer for arbitrary strategy/runtime state."""

    serializer_id = "pickle-v1"

    def dumps(self, state: object) -> bytes:
        return pickle.dumps(state, protocol=pickle.HIGHEST_PROTOCOL)

    def loads(self, payload: bytes) -> object:
        return pickle.loads(payload)


class JsonStateSerializer:
    """JSON serializer for primitive/dict/list dataclass snapshots."""

    serializer_id = "json-v1"

    def dumps(self, state: object) -> bytes:
        return json.dumps(_plain(state), sort_keys=True, separators=(",", ":")).encode("utf-8")

    def loads(self, payload: bytes) -> object:
        return json.loads(payload.decode("utf-8"))


@dataclass(frozen=True)
class BrokerSnapshot:
    cash: float
    equity: float
    peak_equity: float
    max_drawdown: float
    max_drawdown_percent: float
    position: Position
    orders: list[Order] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    closed_trades: list[Trade] = field(default_factory=list)
    open_trades: list[Trade] = field(default_factory=list)
    last_trade_bar: int | None = None


@dataclass(frozen=True)
class RealtimeBrokerSnapshot(BrokerSnapshot):
    """Detached broker checkpoint for future realtime tick rollback.

    Unlike resume snapshots, realtime rollback also needs diagnostics/events to
    return to the previous tick attempt before replaying the next update.
    """

    events: list[Diagnostic] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)
    errors: list[Diagnostic] = field(default_factory=list)


@dataclass(frozen=True)
class RealtimeExecutionCheckpoint:
    """Combined runtime/strategy/broker checkpoint for one realtime tick attempt.

    This is a rollback primitive only. It does not define Pine realtime
    scheduling, varip, or TradingView tick execution semantics.
    """

    broker_state: RealtimeBrokerSnapshot
    runtime_state: object | None = None
    strategy_state: object | None = None


def _plain(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, set):
        return sorted(_plain(v) for v in value)
    return value


def clone_state(value: object | None) -> object | None:
    """Deep-copy state so exported resume snapshots are detached from the live run."""
    return None if value is None else copy.deepcopy(value)


def build_resume_state(
    *,
    bar_index: int,
    config_snapshot_hash: str,
    broker_state: object | None = None,
    strategy_state: object | None = None,
    runtime_state: object | None = None,
    statistics_state: object | None = None,
    random_state: object | None = None,
    metadata: dict[str, Any] | None = None,
) -> BacktestResumeState:
    return BacktestResumeState(
        bar_index=bar_index,
        config_snapshot_hash=config_snapshot_hash,
        strategy_state=clone_state(strategy_state),
        runtime_state=clone_state(runtime_state),
        broker_state=clone_state(broker_state),
        statistics_state=clone_state(statistics_state),
        random_state=clone_state(random_state),
        metadata=dict(metadata or {}),
    )
