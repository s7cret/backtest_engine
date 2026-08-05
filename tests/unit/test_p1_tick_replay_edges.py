from __future__ import annotations

import copy
import operator
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.context import StrategyContext
from backtest_engine.core.engine_validation import validate_backtest_config
from backtest_engine.core.realtime import resolve_realtime_tick_schedule
from backtest_engine.core.realtime_run_loop import (
    _activate_orders,
    _begin_realtime_bar,
    _early_stop_state,
    _prepare_runtime_for_run,
    _require_rollback_state,
)
from backtest_engine.errors import (
    ConfigError,
    ResumeUnsupportedError,
    TickReplayDataError,
    TickReplayStateError,
)
from backtest_engine.models import Bar, Tick


@dataclass
class Runtime:
    normal: int = 0
    varip: dict[str, object] | None = None
    current_bar: Bar | None = None
    ended: int = 0

    def __post_init__(self) -> None:
        if self.varip is None:
            self.varip = {}

    def begin_bar(self, bar: Bar, bar_index: int) -> None:
        del bar_index
        self.current_bar = bar

    def update_realtime_tick(self, tick: object) -> None:
        assert self.current_bar is not None
        price = float(getattr(tick, "price"))
        self.current_bar = Bar(
            self.current_bar.time,
            self.current_bar.open,
            max(self.current_bar.high, price),
            min(self.current_bar.low, price),
            price,
            float(self.current_bar.volume or 0.0) + float(getattr(tick, "volume", 0.0)),
            self.current_bar.time_close,
        )

    def end_bar(self) -> None:
        self.ended += 1

    def export_state(self, *, include_varip: bool = True) -> dict[str, object]:
        state: dict[str, object] = {
            "normal": self.normal,
            "current_bar": copy.deepcopy(self.current_bar),
            "ended": self.ended,
        }
        if include_varip:
            state["varip"] = copy.deepcopy(self.varip)
        return state

    def restore_state(self, state: object) -> None:
        assert isinstance(state, dict)
        self.normal = int(state["normal"])
        self.current_bar = copy.deepcopy(state["current_bar"])
        self.ended = int(state["ended"])
        if "varip" in state:
            self.varip = copy.deepcopy(state["varip"])  # type: ignore[assignment]


class SerializableStrategy:
    finalized = False

    def __init__(self, params: dict[str, object], runtime: Runtime, ctx: StrategyContext):
        del params
        self.runtime = runtime
        self.ctx = ctx
        self.ordinary = 0

    def export_state(self) -> dict[str, int]:
        return {"ordinary": self.ordinary}

    def restore_state(self, state: object) -> None:
        assert isinstance(state, dict)
        self.ordinary = int(state["ordinary"])

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        del bar, bar_index


class LegacyEntryFinalizeStrategy(SerializableStrategy):
    def __init__(self, params: dict[str, object], runtime: Runtime):
        self.runtime = runtime
        self.ordinary = 0

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        del bar, bar_index
        assert self.runtime.varip is not None
        if not self.runtime.varip.get("entered"):
            self.runtime.varip["entered"] = True
            self.ctx.entry("L", "long", qty=1)

    def _finalize(self) -> None:
        self.__class__.finalized = True


class NestedRuntimeStrategy(SerializableStrategy):
    def __init__(self, params: dict[str, object], runtime: Runtime, ctx: StrategyContext):
        super().__init__(params, runtime, ctx)
        self._pine_runtime = Runtime()


def config(**kwargs: object) -> BacktestConfig:
    values: dict[str, object] = {
        "symbol": "SYNTH",
        "timeframe": "1",
        "start_time": 100,
        "end_time": 160,
        "commission_type": "none",
        "calc_on_every_tick": True,
        "experimental_intrabar_strategy_mode": True,
        "realtime_ticks": [Tick(100, 10, 1), Tick(159, 10, 1)],
        "runtime": Runtime(),
        "mintick": 1.0,
    }
    values.update(kwargs)
    return BacktestConfig(**values)  # type: ignore[arg-type]


def test_tick_source_validation_provider_edges() -> None:
    both = config(realtime_tick_provider=object())
    with pytest.raises(ConfigError, match="exactly one tick source"):
        validate_backtest_config(both)

    empty_provider = SimpleNamespace(get_ticks=lambda *_args: [])
    empty_cfg = SimpleNamespace(
        realtime_ticks=None,
        realtime_tick_provider=empty_provider,
        symbol="S",
        timeframe="1",
    )
    assert resolve_realtime_tick_schedule(empty_cfg, []) == ()

    raising = SimpleNamespace(get_ticks=lambda *_args: (_ for _ in ()).throw(OSError("down")))
    raising_cfg = SimpleNamespace(
        realtime_ticks=None,
        realtime_tick_provider=raising,
        symbol="S",
        timeframe="1",
    )
    with pytest.raises(TickReplayDataError, match="get_ticks failed"):
        resolve_realtime_tick_schedule(raising_cfg, [Bar(100, 10, 10, 10, 10, 1, 160)])

    bad = SimpleNamespace(get_ticks=lambda *_args: 1)
    bad_cfg = SimpleNamespace(
        realtime_ticks=None,
        realtime_tick_provider=bad,
        symbol="S",
        timeframe="1",
    )
    with pytest.raises(TickReplayDataError, match="iterable"):
        resolve_realtime_tick_schedule(bad_cfg, [Bar(100, 10, 10, 10, 10, 1, 160)])

    no_ticks = SimpleNamespace(
        realtime_ticks=[], realtime_tick_provider=None, symbol="S", timeframe="1"
    )
    with pytest.raises(TickReplayDataError, match="no ticks"):
        resolve_realtime_tick_schedule(no_ticks, [Bar(100, 10, 10, 10, 10, 1, 160)])


def test_runtime_state_and_begin_bar_fail_closed_edges() -> None:
    strategy = SerializableStrategy({}, Runtime(), SimpleNamespace())  # type: ignore[arg-type]
    with pytest.raises(TickReplayStateError, match="runtime must implement"):
        _require_rollback_state(strategy, object())

    bad_signature = SimpleNamespace(
        export_state=sys.getsizeof, restore_state=lambda _state: None
    )
    with pytest.raises(TickReplayStateError, match="include_varip"):
        _require_rollback_state(strategy, bad_signature)

    no_kw_builtin = SimpleNamespace(
        export_state=operator.itemgetter(0), restore_state=lambda _state: None
    )
    with pytest.raises(TickReplayStateError, match="include_varip"):
        _require_rollback_state(strategy, no_kw_builtin)

    no_kw = SimpleNamespace(export_state=lambda: {}, restore_state=lambda _state: None)
    with pytest.raises(TickReplayStateError, match="include_varip"):
        _require_rollback_state(strategy, no_kw)

    seen: list[object] = []
    _begin_realtime_bar(SimpleNamespace(begin_bar=lambda value: seen.append(value)), Bar(1, 1, 1, 1, 1), 0)
    assert len(seen) == 1
    with pytest.raises(TickReplayStateError, match="begin_realtime_bar"):
        _begin_realtime_bar(object(), Bar(1, 1, 1, 1, 1), 0)


def test_fresh_runtime_baseline_fail_closed_edges() -> None:
    engine = SimpleNamespace()
    bad_export = SimpleNamespace(
        export_state=lambda *, include_varip=True: (_ for _ in ()).throw(
            ValueError("bad export")
        ),
        restore_state=lambda _state: None,
    )
    with pytest.raises(TickReplayStateError, match="could not export"):
        _prepare_runtime_for_run(engine, bad_export)

    bad_restore = SimpleNamespace(
        export_state=lambda *, include_varip=True: {},
        restore_state=lambda _state: None,
    )
    _prepare_runtime_for_run(engine, bad_restore)
    bad_restore.restore_state = lambda _state: (_ for _ in ()).throw(
        ValueError("bad restore")
    )
    with pytest.raises(TickReplayStateError, match="could not restore"):
        _prepare_runtime_for_run(engine, bad_restore)


def test_nested_script_runtime_gets_its_own_fresh_baseline() -> None:
    cfg = config()
    result = BacktestEngine(cfg).run(
        NestedRuntimeStrategy,
        bars=[Bar(100, 10, 10, 10, 10, 2, 160)],
    )
    assert result.status == "completed"


def test_order_activation_and_early_stop_edges() -> None:
    order = SimpleNamespace(id="O", status="pending", active_from_bar_index=1)
    events: list[tuple[object, ...]] = []
    callbacks: list[object] = []
    engine = SimpleNamespace(
        orders=[order],
        _event=lambda *args: events.append(args),
        _cb=lambda _name, value: callbacks.append(value),
    )
    _activate_orders(engine, Bar(1, 1, 1, 1, 1), 1)
    assert order.status == "active"
    assert events and callbacks == [order]

    extremes = SimpleNamespace(drawdown=5.0, drawdown_percent=5.0)
    base = dict(
        _early_stop_enabled=True,
        equity=100.0,
        _min_equity_stop=None,
        _max_drawdown_stop_percent=None,
        _max_drawdown_stop_cash=None,
        _max_bars_without_trade=None,
        last_trade_bar=None,
    )
    assert _early_stop_state(SimpleNamespace(**base), 3, extremes) == (False, "completed", None)
    assert _early_stop_state(SimpleNamespace(**(base | {"_min_equity_stop": 100.0})), 3, extremes)[2] == "min_equity_stop"
    assert _early_stop_state(SimpleNamespace(**(base | {"_max_drawdown_stop_percent": 5.0})), 3, extremes)[2] == "max_drawdown_stop_percent"
    assert _early_stop_state(SimpleNamespace(**(base | {"_max_drawdown_stop_cash": 5.0})), 3, extremes)[2] == "max_drawdown_stop_cash"
    assert _early_stop_state(
        SimpleNamespace(**(base | {"_max_bars_without_trade": 2, "last_trade_bar": 1})),
        3,
        extremes,
    )[2] == "max_bars_without_trade"


def test_missing_runtime_tick_update_is_typed() -> None:
    engine = BacktestEngine(config())
    runtime = SimpleNamespace(restore_state=lambda _state: None)
    strategy = SimpleNamespace(restore_state=lambda _state: None)
    engine._realtime_script_runtime = runtime
    engine._realtime_runtime_checkpoint = {}
    engine._realtime_strategy_checkpoint = {}
    engine._realtime_tick_parent = Bar(100, 10, 10, 10, 10, 1, 160)
    engine._realtime_tick_prefix = (Tick(100, 10, 1),)
    engine._realtime_tick_is_final = True
    with pytest.raises(ResumeUnsupportedError, match="update_realtime_tick"):
        engine._prepare_realtime_strategy_invocation(strategy)


def test_combined_realtime_lifecycle_branches() -> None:
    LegacyEntryFinalizeStrategy.finalized = False
    result = BacktestEngine(
        config(
            score_start_time=100,
            score_end_time=160,
            process_orders_on_close=True,
            early_stop_enabled=True,
            min_equity_stop=10_000.0,
            force_close_on_end=True,
        )
    ).run(
        LegacyEntryFinalizeStrategy,
        bars=[Bar(100, 10, 10, 10, 10, 2, 160)],
    )
    assert result.status == "early_stopped"
    assert LegacyEntryFinalizeStrategy.finalized is True

    score_bars = [
        Bar(100, 10, 10, 10, 10, 1, 160),
        Bar(160, 11, 11, 11, 11, 1, 220),
    ]
    score_ticks = [Tick(100, 10, 1), Tick(160, 11, 1)]
    scored = BacktestEngine(
        config(
            end_time=220,
            realtime_ticks=score_ticks,
            score_start_time=160,
            score_end_time=220,
        )
    ).run(SerializableStrategy, bars=score_bars)
    assert scored.status == "completed"
    assert scored.score_max_drawdown == 0.0
