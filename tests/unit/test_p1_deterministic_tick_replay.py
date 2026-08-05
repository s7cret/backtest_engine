from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import Any

import pytest

import backtest_engine.core.resume_state as resume_state_module
from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.core.deterministic_hash import sha256_obj
from backtest_engine.core.realtime import (
    BarTickSlice,
    _validate_tick_slice_reconstructs_bar,
    build_bar_tick_schedule,
)
from backtest_engine.core.resume_state import bar_prefix_fingerprint
from backtest_engine.core.state_snapshot import BrokerSnapshot
from backtest_engine.core.validation import data_fingerprint
from backtest_engine.errors import (
    ConfigError,
    ResumeUnsupportedError,
    TickReplayDataError,
    TickReplayStateError,
)
from backtest_engine.models import Bar, BarSeries, Tick


@dataclass
class SyntheticRealtimeRuntime:
    normal: int = 0
    varip: dict[str, object] | None = None
    current_bar: Bar | None = None
    ended_bars: int = 0

    def __post_init__(self) -> None:
        if self.varip is None:
            self.varip = {}

    def begin_realtime_bar(self, bar: Bar) -> None:
        self.current_bar = bar

    def update_realtime_tick(self, tick: object) -> Bar:
        assert self.current_bar is not None
        price = float(getattr(tick, "price"))
        volume = float(getattr(tick, "volume", 0.0))
        self.current_bar = Bar(
            time=self.current_bar.time,
            open=self.current_bar.open,
            high=max(self.current_bar.high, price),
            low=min(self.current_bar.low, price),
            close=price,
            volume=float(self.current_bar.volume or 0.0) + volume,
            time_close=self.current_bar.time_close,
        )
        return self.current_bar

    def end_bar(self) -> None:
        self.ended_bars += 1

    def export_state(self, *, include_varip: bool = True) -> dict[str, object]:
        state: dict[str, object] = {
            "normal": self.normal,
            "current_bar": copy.deepcopy(self.current_bar),
            "ended_bars": self.ended_bars,
        }
        if include_varip:
            state["varip"] = copy.deepcopy(self.varip)
        return state

    def restore_state(self, state: object) -> None:
        assert isinstance(state, dict)
        self.normal = int(state["normal"])
        self.current_bar = copy.deepcopy(state["current_bar"])
        self.ended_bars = int(state["ended_bars"])
        if "varip" in state:
            value = state["varip"]
            assert isinstance(value, dict)
            self.varip = copy.deepcopy(value)


class SerializableTickStrategy:
    def __init__(self, params, runtime, ctx):
        del params
        self.runtime = runtime
        self.ctx = ctx
        self.ordinary = 0

    def export_state(self) -> dict[str, int]:
        return {"ordinary": self.ordinary}

    def restore_state(self, state: object) -> None:
        assert isinstance(state, dict)
        self.ordinary = int(state["ordinary"])


class RollbackProbeStrategy(SerializableTickStrategy):
    trace: list[tuple[int, float, int, int, bool]] = []

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        self.ordinary += 1
        self.runtime.normal += 1
        assert self.runtime.varip is not None
        count = int(self.runtime.varip.get("count", 0)) + 1
        self.runtime.varip["count"] = count
        self.__class__.trace.append(
            (bar_index, bar.close, self.ordinary, self.runtime.normal, count == 1)
        )


class NoStateStrategy:
    def __init__(self, params, runtime, ctx):
        del params, runtime, ctx

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        del bar, bar_index


class SerializableBarRuntime(SyntheticRealtimeRuntime):
    def begin_bar(self, bar: Bar, bar_index: int) -> None:
        del bar_index
        self.current_bar = bar


class SerializableBarStrategy(SerializableTickStrategy):
    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        del bar, bar_index


def _config(
    bars: list[Bar],
    ticks: list[Tick],
    runtime: SyntheticRealtimeRuntime | None = None,
    **kwargs: object,
) -> BacktestConfig:
    values: dict[str, object] = {
        "symbol": "SYNTH",
        "timeframe": "1",
        "start_time": bars[0].time,
        "end_time": int(bars[-1].time_close or bars[-1].time),
        "commission_type": "none",
        "commission_value": 0.0,
        "calc_on_every_tick": True,
        "experimental_intrabar_strategy_mode": True,
        "realtime_ticks": ticks,
        "runtime": runtime or SyntheticRealtimeRuntime(),
        "mintick": 1.0,
        "content_hash_include_equity_curve": False,
    }
    values.update(kwargs)
    return BacktestConfig(**values)  # type: ignore[arg-type]


def _legacy_config(bars: list[Bar]) -> BacktestConfig:
    return BacktestConfig(
        symbol="SYNTH",
        timeframe="1",
        start_time=bars[0].time,
        end_time=int(bars[-1].time_close or bars[-1].time),
        commission_type="none",
        runtime=SerializableBarRuntime(),
        export_resume_state=True,
    )


def test_calc_on_every_tick_rolls_back_ordinary_state_and_preserves_varip() -> None:
    bar = Bar(100, 10, 12, 9, 11, 6, 160)
    ticks = [Tick(100, 10, 1), Tick(120, 12, 2), Tick(140, 9, 1), Tick(159, 11, 2)]
    runtime = SyntheticRealtimeRuntime()
    RollbackProbeStrategy.trace = []

    result = BacktestEngine(_config([bar], ticks, runtime)).run(
        RollbackProbeStrategy, bars=[bar]
    )

    assert result.status == "completed"
    assert [row[1] for row in RollbackProbeStrategy.trace] == [10, 12, 9, 11]
    assert [row[2] for row in RollbackProbeStrategy.trace] == [1, 1, 1, 1]
    assert [row[3] for row in RollbackProbeStrategy.trace] == [1, 1, 1, 1]
    assert [row[4] for row in RollbackProbeStrategy.trace] == [
        True,
        False,
        False,
        False,
    ]
    assert runtime.varip == {"count": 4}
    assert runtime.ended_bars == 1


def test_tick_replay_requires_explicit_serializable_strategy_state() -> None:
    bar = Bar(100, 10, 10, 10, 10, 1, 160)
    ticks = [Tick(100, 10, 1)]

    with pytest.raises(TickReplayStateError, match="export_state.*restore_state"):
        BacktestEngine(_config([bar], ticks)).run(NoStateStrategy, bars=[bar])


def test_tick_replay_fails_closed_when_ticks_do_not_reconstruct_parent_ohlc() -> None:
    bar = Bar(100, 10, 12, 9, 11, 4, 160)
    incomplete = [Tick(100, 10, 1), Tick(159, 11, 3)]

    with pytest.raises(TickReplayDataError, match="reconstruct parent OHLC"):
        BacktestEngine(_config([bar], incomplete)).run(
            RollbackProbeStrategy, bars=[bar]
        )


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
def test_tick_replay_requires_exact_parent_ohlc_reconstruction(field: str) -> None:
    bar = Bar(100, 10, 12, 9, 11, 4, 160)
    ticks = [Tick(100, 10, 1), Tick(120, 12, 1), Tick(140, 9, 1), Tick(159, 11, 1)]
    delta = -5e-13 if field == "low" else 5e-13
    changed_bar = replace(bar, **{field: getattr(bar, field) + delta})

    with pytest.raises(TickReplayDataError, match="reconstruct parent OHLC"):
        BacktestEngine(_config([changed_bar], ticks)).run(
            RollbackProbeStrategy, bars=[changed_bar]
        )


def test_legacy_bar_data_fingerprint_preserves_v4_payload_identity() -> None:
    series = BarSeries.from_bars([Bar(100, 10, 12, 9, 11, 4, 160)])
    legacy_payload = {
        "time": [100],
        "open": [10],
        "high": [12],
        "low": [9],
        "close": [11],
        "volume": [4],
    }

    assert data_fingerprint(series) == sha256_obj(legacy_payload)


class OneOrderStrategy(SerializableTickStrategy):
    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        del bar_index
        assert self.runtime.varip is not None
        if self.runtime.varip.get("submitted"):
            return
        self.runtime.varip["submitted"] = True
        kind = str(self.ctx.config.strategy_fingerprint)
        direction = "short" if kind.endswith("_short") else "long"
        if kind.startswith("market"):
            self.ctx.entry("E", direction, qty=1)
        elif kind.startswith("limit"):
            self.ctx.entry(
                "E", direction, qty=1, limit=9 if direction == "long" else 11
            )
        elif kind.startswith("stop_limit"):
            self.ctx.entry(
                "E",
                direction,
                qty=1,
                stop=11 if direction == "long" else 9,
                limit=10,
            )
        elif kind.startswith("stop"):
            self.ctx.entry("E", direction, qty=1, stop=11 if direction == "long" else 9)
        else:  # pragma: no cover - guarded by test cases
            raise AssertionError(kind)


class TickFillPhaseProbeStrategy(SerializableTickStrategy):
    positions: list[float] = []

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        del bar, bar_index
        self.__class__.positions.append(self.ctx.state.position_size)
        assert self.runtime.varip is not None
        if not self.runtime.varip.get("submitted"):
            self.runtime.varip["submitted"] = True
            self.ctx.entry("E", "long", qty=1)


@pytest.mark.parametrize("mode", ["process_orders_on_close", "close_only"])
def test_close_phase_modes_wait_for_final_synthetic_tick(mode: str) -> None:
    bar = Bar(100, 10, 12, 10, 11, 3, 160)
    ticks = [Tick(100, 10, 1), Tick(120, 12, 1), Tick(159, 11, 1)]
    kwargs: dict[str, object] = (
        {"process_orders_on_close": True}
        if mode == "process_orders_on_close"
        else {"fill_model": "close_only"}
    )
    TickFillPhaseProbeStrategy.positions = []

    result = BacktestEngine(_config([bar], ticks, **kwargs)).run(
        TickFillPhaseProbeStrategy, bars=[bar]
    )

    assert TickFillPhaseProbeStrategy.positions == [0.0, 0.0, 1.0]
    assert (result.open_trades or [])[0].entry_time == ticks[-1].time


def test_standard_tick_fill_preserves_next_tick_eligibility() -> None:
    bar = Bar(100, 10, 12, 10, 11, 3, 160)
    ticks = [Tick(100, 10, 1), Tick(120, 12, 1), Tick(159, 11, 1)]
    TickFillPhaseProbeStrategy.positions = []

    result = BacktestEngine(_config([bar], ticks)).run(
        TickFillPhaseProbeStrategy, bars=[bar]
    )

    assert TickFillPhaseProbeStrategy.positions == [0.0, 1.0, 1.0]
    assert (result.open_trades or [])[0].entry_time == ticks[1].time


@pytest.mark.parametrize(
    ("kind", "prices", "expected"),
    [
        ("market_long", [10, 11], 11),
        ("market_short", [10, 9], 9),
        ("limit_long", [10, 9, 10], 9),
        ("limit_short", [10, 11, 10], 11),
        ("stop_long", [10, 11, 10], 11),
        ("stop_short", [10, 9, 10], 9),
        ("stop_limit_long", [10, 11, 10], 10),
        ("stop_limit_short", [10, 9, 10], 10),
    ],
)
def test_tick_orders_activate_and_fill_deterministically(
    kind: str, prices: list[float], expected: float
) -> None:
    bar = Bar(100, prices[0], max(prices), min(prices), prices[-1], len(prices), 160)
    ticks = [Tick(100 + i * 10, price, 1) for i, price in enumerate(prices)]
    config = _config([bar], ticks, strategy_fingerprint=kind)

    result = BacktestEngine(config).run(OneOrderStrategy, bars=[bar])

    open_trades = result.open_trades or []
    assert len(open_trades) == 1
    assert open_trades[0].direction == kind.rsplit("_", 1)[1]
    assert open_trades[0].entry_price == expected
    assert (
        len([event for event in result.events or [] if event.code == "ORDER_FILLED"])
        == 1
    )


class TrailingAndReversalStrategy(SerializableTickStrategy):
    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        assert self.runtime.varip is not None
        key = f"submitted:{bar_index}"
        if bar_index == 0 and not self.runtime.varip.get(key):
            self.runtime.varip[key] = True
            self.ctx.entry("L", "long", qty=1)
        elif bar_index == 1 and self.ctx.state.position_size > 0:
            self.ctx.exit("TR", "L", qty=1, trail_price=12, trail_offset=1)
        elif bar_index == 2 and not self.runtime.varip.get(key):
            self.runtime.varip[key] = True
            self.ctx.entry("S", "short", qty=1)
        elif (
            bar_index == 3
            and self.ctx.state.position_size < 0
            and not self.runtime.varip.get(key)
        ):
            self.runtime.varip[key] = True
            self.ctx.entry("L2", "long", qty=1)


def _lifecycle_fixture() -> tuple[list[Bar], list[Tick]]:
    bars = [
        Bar(100, 10, 10, 10, 10, 2, 160),
        Bar(160, 10, 12, 10, 11, 3, 220),
        Bar(220, 11, 11, 10, 10, 2, 280),
        Bar(280, 10, 11, 10, 11, 2, 340),
    ]
    prices = ([10, 10], [10, 12, 11], [11, 10], [10, 11])
    ticks = [
        Tick(bar.time + index * 10, price, 1)
        for bar, bar_prices in zip(bars, prices, strict=True)
        for index, price in enumerate(bar_prices)
    ]
    return bars, ticks


def test_trailing_fill_and_reversal_have_no_duplicate_fills_or_trades() -> None:
    bars, ticks = _lifecycle_fixture()

    result = BacktestEngine(_config(bars, ticks)).run(
        TrailingAndReversalStrategy, bars=bars
    )

    assert [
        (trade.entry_id, trade.exit_id) for trade in result.closed_trades or []
    ] == [
        ("L", "TR:T"),
        ("S", "L2"),
    ]
    assert [trade.exit_price for trade in result.closed_trades or []] == [11, 11]
    fill_events = [
        event for event in result.events or [] if event.code == "ORDER_FILLED"
    ]
    assert len(fill_events) == 4
    assert (
        len({(event.order_id, event.bar_time, event.message) for event in fill_events})
        == 4
    )
    open_trades = result.open_trades or []
    assert len(open_trades) == 1
    assert open_trades[0].entry_id == "L2"


class FillRecalcStrategy(SerializableTickStrategy):
    calls: list[tuple[float, float]] = []

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        del bar_index
        self.__class__.calls.append((bar.close, self.ctx.state.position_size))
        assert self.runtime.varip is not None
        if not self.runtime.varip.get("entry_submitted"):
            self.runtime.varip["entry_submitted"] = True
            self.ctx.entry("L", "long", qty=1)
        elif self.ctx.state.position_size > 0 and not self.runtime.varip.get(
            "close_submitted"
        ):
            self.runtime.varip["close_submitted"] = True
            self.ctx.close("L")


def test_calc_on_order_fills_recalculates_at_fill_price_before_tick_execution() -> None:
    bar = Bar(100, 10, 11, 10, 11, 2, 160)
    ticks = [Tick(100, 10, 1), Tick(120, 11, 1)]
    FillRecalcStrategy.calls = []

    result = BacktestEngine(_config([bar], ticks, calc_on_order_fills=True)).run(
        FillRecalcStrategy, bars=[bar]
    )

    assert [
        (trade.entry_price, trade.exit_price) for trade in result.closed_trades or []
    ] == [(11, 11)]
    assert FillRecalcStrategy.calls == [
        (10, 0.0),
        (11, 1.0),
        (11, 0.0),
        (11, 0.0),
    ]
    assert (
        len([event for event in result.events or [] if event.code == "ORDER_FILLED"])
        == 2
    )


def test_tick_replay_result_hash_is_stable_across_repeated_runs() -> None:
    bars, ticks = _lifecycle_fixture()

    first = BacktestEngine(_config(bars, ticks)).run(
        TrailingAndReversalStrategy, bars=bars
    )
    second = BacktestEngine(_config(bars, ticks)).run(
        TrailingAndReversalStrategy, bars=bars
    )

    assert first.content_hash_value == second.content_hash_value


def test_tick_schedule_fingerprint_is_in_config_data_and_content_identity() -> None:
    bar = Bar(100, 10, 12, 9, 11, 4, 160)
    ticks = [
        Tick(100, 10, 1, bid=9.5, ask=10.5),
        Tick(120, 12, 1, bid=11.5, ask=12.5),
        Tick(140, 9, 1, bid=8.5, ask=9.5),
        Tick(159, 11, 1, bid=10.5, ask=11.5),
    ]
    reordered = [
        ticks[0],
        Tick(120, 9, 1, bid=8.5, ask=9.5),
        Tick(140, 12, 1, bid=11.5, ask=12.5),
        ticks[3],
    ]

    first = BacktestEngine(_config([bar], ticks)).run(RollbackProbeStrategy, bars=[bar])
    changed = BacktestEngine(_config([bar], reordered)).run(
        RollbackProbeStrategy, bars=[bar]
    )

    first_schedule_hash = first.config_snapshot["realtime_tick_schedule_fingerprint"]
    assert len(first_schedule_hash) == 64
    assert (
        first_schedule_hash
        != changed.config_snapshot["realtime_tick_schedule_fingerprint"]
    )
    assert first.data_fingerprint != changed.data_fingerprint
    assert first.content_hash_value != changed.content_hash_value


def test_configured_data_fingerprint_is_bound_to_tick_schedule() -> None:
    bar = Bar(100, 10, 10, 10, 10, 2, 160)
    ticks = [Tick(100, 10, 1), Tick(159, 10, 1)]
    config = _config([bar], ticks)
    config.data_fingerprint = "external-bars-fingerprint"

    result = BacktestEngine(config).run(RollbackProbeStrategy, bars=[bar])

    assert result.data_fingerprint != config.data_fingerprint
    assert result.data_fingerprint is not None
    assert len(result.data_fingerprint) == 64


def test_tick_schedule_resume_mismatch_warn_policy_is_diagnostic() -> None:
    bars, ticks = _lifecycle_fixture()
    partial_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], partial_ticks, export_resume_state=True)
    partial_config.end_time = int(bars[-1].time_close or bars[-1].time)
    first = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy, bars=bars[:2]
    )
    assert first.resume_state is not None
    changed_ticks = list(ticks)
    changed_ticks[0] = Tick(ticks[0].time + 1, ticks[0].price, ticks[0].volume)
    config = _config(bars, changed_ticks)
    config.resume_validation_policy = "diagnostic"
    engine = BacktestEngine(config)

    engine.run(
        TrailingAndReversalStrategy,
        bars=bars,
        resume_state=first.resume_state,
    )

    assert any(item.code == "RESUME_TICK_SCHEDULE_MISMATCH" for item in engine.warnings)


@pytest.mark.parametrize(
    "replacement",
    [
        Tick(101, 10, 1, bid=9, ask=11),
        Tick(100, 10, 2, bid=9, ask=11),
        Tick(100, 10, 1, bid=8, ask=11),
        Tick(100, 10, 1, bid=9, ask=12),
    ],
)
def test_tick_schedule_fingerprint_covers_every_tick_field(replacement: Tick) -> None:
    bar = Bar(100, 10, 10, 10, 10, 2, 160)
    baseline = [Tick(100, 10, 1, bid=9, ask=11), Tick(159, 10, 1)]
    changed_tail_volume = 0 if replacement.volume == 2 else 1
    changed = [replacement, Tick(159, 10, changed_tail_volume)]

    first = BacktestEngine(_config([bar], baseline)).run(
        RollbackProbeStrategy, bars=[bar]
    )
    second = BacktestEngine(_config([bar], changed)).run(
        RollbackProbeStrategy, bars=[bar]
    )

    assert (
        first.config_snapshot["realtime_tick_schedule_fingerprint"]
        != second.config_snapshot["realtime_tick_schedule_fingerprint"]
    )


def test_tick_replay_resume_rejects_changed_processed_tick_sequence() -> None:
    bars, ticks = _lifecycle_fixture()
    partial_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], partial_ticks, export_resume_state=True)
    partial_config.end_time = int(bars[-1].time_close or bars[-1].time)
    first = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy, bars=bars[:2]
    )
    assert first.resume_state is not None
    changed_ticks = list(ticks)
    changed_ticks[0] = Tick(ticks[0].time + 1, ticks[0].price, ticks[0].volume)

    with pytest.raises(ResumeUnsupportedError, match="tick schedule fingerprint"):
        BacktestEngine(_config(bars, changed_ticks)).run(
            TrailingAndReversalStrategy,
            bars=bars,
            resume_state=first.resume_state,
        )


def test_strict_tick_resume_rejects_missing_schedule_fingerprint() -> None:
    bars, ticks = _lifecycle_fixture()
    partial_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], partial_ticks, export_resume_state=True)
    partial_config.end_time = int(bars[-1].time_close or bars[-1].time)
    first = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy, bars=bars[:2]
    )
    assert first.resume_state is not None
    missing_fingerprint = replace(
        first.resume_state,
        metadata={"resume_contract": "engine-broker-snapshot-v1"},
    )

    with pytest.raises(
        ResumeUnsupportedError, match="missing tick schedule fingerprint"
    ):
        BacktestEngine(_config(bars, ticks)).run(
            TrailingAndReversalStrategy,
            bars=bars,
            resume_state=missing_fingerprint,
        )


def test_strict_tick_resume_rejects_missing_statistics_state() -> None:
    bars, ticks = _lifecycle_fixture()
    partial_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], partial_ticks, export_resume_state=True)
    partial_config.end_time = int(bars[-1].time_close or bars[-1].time)
    first = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy, bars=bars[:2]
    )
    assert first.resume_state is not None
    missing_statistics = replace(first.resume_state, statistics_state=None)

    with pytest.raises(ResumeUnsupportedError, match="missing statistics_state"):
        BacktestEngine(_config(bars, ticks)).run(
            TrailingAndReversalStrategy,
            bars=bars,
            resume_state=missing_statistics,
        )


def test_legacy_bar_resume_without_bar_prefix_fingerprint_fails_closed() -> None:
    bars = [
        Bar(100, 10, 10, 10, 10, 1, 160),
        Bar(160, 11, 11, 11, 11, 1, 220),
        Bar(220, 12, 12, 12, 12, 1, 280),
    ]
    first = BacktestEngine(_legacy_config(bars)).run(
        SerializableBarStrategy, bars=bars[:2]
    )
    assert first.resume_state is not None
    legacy_state = replace(
        first.resume_state,
        statistics_state=None,
        metadata={"resume_contract": "engine-broker-snapshot-v1"},
    )

    with pytest.raises(ResumeUnsupportedError, match="bar prefix fingerprint"):
        BacktestEngine(_legacy_config(bars)).run(
            SerializableBarStrategy,
            bars=bars,
            resume_state=legacy_state,
        )


def test_tick_replay_resume_matches_uninterrupted_result_hash() -> None:
    bars, ticks = _lifecycle_fixture()
    full_config = _config(bars, ticks, export_resume_state=True)
    uninterrupted = BacktestEngine(full_config).run(
        TrailingAndReversalStrategy, bars=bars
    )

    first_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], first_ticks, export_resume_state=True)
    partial_config.end_time = full_config.end_time
    first = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy, bars=bars[:2]
    )
    assert first.resume_state is not None

    resumed = BacktestEngine(_config(bars, ticks, export_resume_state=True)).run(
        TrailingAndReversalStrategy,
        bars=bars,
        resume_state=first.resume_state,
    )

    assert resumed.content_hash_value == uninterrupted.content_hash_value
    assert resumed.closed_trades == uninterrupted.closed_trades
    assert resumed.open_trades == uninterrupted.open_trades


def test_tick_replay_resume_restores_score_window_history_and_statistics() -> None:
    bars, ticks = _lifecycle_fixture()
    full_kwargs: dict[str, object] = {
        "export_resume_state": True,
        "score_start_time": bars[1].time,
        "score_end_time": int(bars[-1].time_close or bars[-1].time),
        "content_hash_include_equity_curve": True,
    }
    uninterrupted = BacktestEngine(_config(bars, ticks, **full_kwargs)).run(
        TrailingAndReversalStrategy, bars=bars
    )

    partial_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], partial_ticks, **full_kwargs)
    partial_config.end_time = int(bars[-1].time_close or bars[-1].time)
    first = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy, bars=bars[:2]
    )
    assert first.resume_state is not None
    assert isinstance(first.resume_state.statistics_state, dict)
    assert len(first.resume_state.statistics_state["score_equity_points"]) == 1
    assert first.resume_state.statistics_state["closed_trade_stats_count"] == 1

    resumed = BacktestEngine(_config(bars, ticks, **full_kwargs)).run(
        TrailingAndReversalStrategy,
        bars=bars,
        resume_state=first.resume_state,
    )

    assert resumed.equity_curve == uninterrupted.equity_curve
    assert resumed.bars_processed == uninterrupted.bars_processed == 3
    assert (
        resumed.score_max_drawdown,
        resumed.score_max_drawdown_percent,
        resumed.score_max_runup,
        resumed.score_max_runup_percent,
        resumed.score_sharpe_ratio,
        resumed.score_sortino_ratio,
    ) == (
        uninterrupted.score_max_drawdown,
        uninterrupted.score_max_drawdown_percent,
        uninterrupted.score_max_runup,
        uninterrupted.score_max_runup_percent,
        uninterrupted.score_sharpe_ratio,
        uninterrupted.score_sortino_ratio,
    )
    assert resumed.content_hash_value == uninterrupted.content_hash_value


def test_tick_provider_is_called_and_malformed_provider_fails_with_typed_error() -> (
    None
):
    bar = Bar(100, 10, 10, 10, 10, 1, 160)
    tick = Tick(100, 10, 1)

    class Provider:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int, int]] = []

        def get_ticks(self, symbol: str, timeframe: str, start: int, end: int):
            self.calls.append((symbol, timeframe, start, end))
            return [tick]

    provider = Provider()
    config = _config([bar], [tick])
    config.realtime_ticks = None
    config.realtime_tick_provider = provider
    BacktestEngine(config).run(RollbackProbeStrategy, bars=[bar])
    assert provider.calls == [("SYNTH", "1", 100, 160)]

    config.realtime_tick_provider = object()
    with pytest.raises(TickReplayDataError, match="get_ticks"):
        BacktestEngine(config).run(RollbackProbeStrategy, bars=[bar])


def test_calc_on_every_tick_still_requires_explicit_opt_in() -> None:
    bar = Bar(100, 10, 10, 10, 10, 1, 160)
    config = _config([bar], [Tick(100, 10, 1)])
    config.experimental_intrabar_strategy_mode = False

    with pytest.raises(ConfigError, match="experimental_intrabar_strategy_mode"):
        BacktestEngine(config).run(RollbackProbeStrategy, bars=[bar])


def test_tick_replay_exact_ohlc_does_not_collapse_large_integers_to_float() -> None:
    parent = 2**53
    tick_price = parent + 1
    bar = Bar(100, parent, parent, parent, parent, 1, 160)

    with pytest.raises(TickReplayDataError, match="reconstruct parent OHLC"):
        BacktestEngine(_config([bar], [Tick(100, tick_price, 1)])).run(
            RollbackProbeStrategy, bars=[bar]
        )


@pytest.mark.parametrize("field", ["strategy_state", "runtime_state"])
def test_strict_tick_resume_requires_script_and_runtime_state(field: str) -> None:
    bars, ticks = _lifecycle_fixture()
    partial_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], partial_ticks, export_resume_state=True)
    partial_config.end_time = int(bars[-1].time_close or bars[-1].time)
    first = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy, bars=bars[:2]
    )
    assert first.resume_state is not None
    incomplete = replace(first.resume_state, **{field: None})

    with pytest.raises(ResumeUnsupportedError, match=field):
        BacktestEngine(_config(bars, ticks)).run(
            TrailingAndReversalStrategy, bars=bars, resume_state=incomplete
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("equity_curve", (), "must be a list"),
        ("engine_win_trades_total", True, "non-negative integer"),
        ("engine_gross_profit_total", float("nan"), "finite and non-negative"),
    ],
)
def test_strict_tick_resume_validates_complete_statistics_schema(
    field: str, value: object, message: str
) -> None:
    bars, ticks = _lifecycle_fixture()
    partial_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], partial_ticks, export_resume_state=True)
    partial_config.end_time = int(bars[-1].time_close or bars[-1].time)
    first = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy, bars=bars[:2]
    )
    assert first.resume_state is not None
    assert isinstance(first.resume_state.statistics_state, dict)
    statistics = dict(first.resume_state.statistics_state)
    statistics[field] = value
    invalid = replace(first.resume_state, statistics_state=statistics)

    with pytest.raises(ResumeUnsupportedError, match=message):
        BacktestEngine(_config(bars, ticks)).run(
            TrailingAndReversalStrategy, bars=bars, resume_state=invalid
        )


def test_strict_tick_resume_rejects_empty_statistics_schema() -> None:
    bars, ticks = _lifecycle_fixture()
    partial_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], partial_ticks, export_resume_state=True)
    partial_config.end_time = int(bars[-1].time_close or bars[-1].time)
    first = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy, bars=bars[:2]
    )
    assert first.resume_state is not None

    with pytest.raises(ResumeUnsupportedError, match="missing fields"):
        BacktestEngine(_config(bars, ticks)).run(
            TrailingAndReversalStrategy,
            bars=bars,
            resume_state=replace(first.resume_state, statistics_state={}),
        )


def test_tick_replay_fails_closed_when_tick_volume_does_not_match_parent() -> None:
    bar = Bar(100, 10, 12, 9, 11, 999, 160)
    ticks = [
        Tick(100, 10, 2),
        Tick(120, 12, 3),
        Tick(140, 9, 1),
        Tick(159, 11, 4),
    ]

    with pytest.raises(TickReplayDataError, match="volume"):
        BacktestEngine(_config([bar], ticks)).run(SerializableTickStrategy, bars=[bar])


def test_tick_schedule_rejects_malformed_tick_with_domain_error() -> None:
    bar = Bar(100, 10, 10, 10, 10, 1, 160)
    with pytest.raises(ConfigError, match="Tick"):
        build_bar_tick_schedule([bar], [object()])  # type: ignore[list-item]


@pytest.mark.parametrize(
    ("tick", "message"),
    [
        (Tick(True, 10, 1), "time must be an integer"),
        (Tick(100, True, 1), "price must be finite"),
        (Tick(100, None, 1), "price must be finite"),  # type: ignore[arg-type]
        (Tick(100, object(), 1), "price must be finite"),  # type: ignore[arg-type]
        (Tick(100, 10, -1), "volume must be non-negative"),
        (Tick(100, 10, 1, bid=11, ask=10), "bid must be less than or equal to ask"),
    ],
)
def test_tick_schedule_rejects_each_noncanonical_tick_shape(
    tick: Tick, message: str
) -> None:
    bar = Bar(100, 10, 10, 10, 10, 1, 160)
    with pytest.raises(ConfigError, match=message):
        build_bar_tick_schedule([bar], [tick])


def test_tick_slice_validation_rejects_invalid_parent_and_tick_volume() -> None:
    tick = Tick(100, 10, 1)
    with pytest.raises(TickReplayDataError, match="parent volume"):
        _validate_tick_slice_reconstructs_bar(
            BarTickSlice(0, Bar(100, 10, 10, 10, 10, float("nan"), 160), (tick,))
        )

    invalid_volume = Tick(100, 10, object())  # type: ignore[arg-type]
    with pytest.raises(TickReplayDataError, match="tick 0 volume"):
        _validate_tick_slice_reconstructs_bar(
            BarTickSlice(0, Bar(100, 10, 10, 10, 10, 1, 160), (invalid_volume,))
        )


def test_diagnostic_resume_reports_changed_bar_prefix() -> None:
    bars = [
        Bar(100, 10, 10, 10, 10, 1, 160),
        Bar(160, 11, 11, 11, 11, 1, 220),
        Bar(220, 12, 12, 12, 12, 1, 280),
    ]
    first = BacktestEngine(_legacy_config(bars)).run(
        SerializableBarStrategy, bars=bars[:2]
    )
    assert first.resume_state is not None
    changed = [replace(bars[0], volume=2), *bars[1:]]
    diagnostic_config = _legacy_config(changed)
    diagnostic_config.resume_validation_policy = "diagnostic"
    engine = BacktestEngine(diagnostic_config)

    engine.run(
        SerializableBarStrategy,
        bars=changed,
        resume_state=first.resume_state,
    )

    assert any(item.code == "RESUME_BAR_PREFIX_MISMATCH" for item in engine.warnings)


def test_bar_resume_preserves_full_equity_curve_and_content_identity() -> None:
    bars = [
        Bar(100, 10, 10, 10, 10, 1, 160),
        Bar(160, 11, 11, 11, 11, 1, 220),
        Bar(220, 12, 12, 12, 12, 1, 280),
    ]
    uninterrupted = BacktestEngine(_legacy_config(bars)).run(
        SerializableBarStrategy, bars=bars
    )
    prefix = BacktestEngine(_legacy_config(bars)).run(
        SerializableBarStrategy, bars=bars[:2]
    )
    assert prefix.resume_state is not None

    resumed = BacktestEngine(_legacy_config(bars)).run(
        SerializableBarStrategy, bars=bars, resume_state=prefix.resume_state
    )

    assert resumed.equity_curve == uninterrupted.equity_curve
    assert resumed.content_hash_value == uninterrupted.content_hash_value


@pytest.mark.parametrize(("cursor", "prefix_count"), [(True, 2), (-2, 0), (3, 4)])
def test_resume_rejects_invalid_bar_cursor_before_restore(
    cursor: object, prefix_count: int
) -> None:
    bars = [
        Bar(100, 10, 10, 10, 10, 1, 160),
        Bar(160, 11, 11, 11, 11, 1, 220),
        Bar(220, 12, 12, 12, 12, 1, 280),
    ]
    prefix = BacktestEngine(_legacy_config(bars)).run(
        SerializableBarStrategy, bars=bars[:2]
    )
    assert prefix.resume_state is not None
    metadata = dict(prefix.resume_state.metadata)
    metadata["bar_prefix_fingerprint"] = bar_prefix_fingerprint(
        BarSeries.from_bars(bars), prefix_count
    )
    invalid = replace(prefix.resume_state, bar_index=cursor, metadata=metadata)
    engine = BacktestEngine(_legacy_config(bars))

    with pytest.raises(ResumeUnsupportedError, match="bar_index"):
        engine.run(SerializableBarStrategy, bars=bars, resume_state=invalid)

    assert engine.cash == engine.config.initial_capital
    assert engine.orders == []


def test_fresh_bar_run_does_not_inherit_previous_equity_history() -> None:
    bars, _ = _lifecycle_fixture()
    engine = BacktestEngine(_legacy_config(bars))

    first = engine.run(SerializableBarStrategy, bars=bars)
    second = engine.run(SerializableBarStrategy, bars=bars)

    assert [point.bar_index for point in first.equity_curve or []] == list(
        range(len(bars))
    )
    assert second.equity_curve == first.equity_curve


@pytest.mark.parametrize("corruption", ["missing", "empty_equity", "wrong_index"])
def test_strict_bar_resume_rejects_corrupt_statistics_before_restore(
    corruption: str,
) -> None:
    bars, _ = _lifecycle_fixture()
    prefix = BacktestEngine(_legacy_config(bars)).run(
        SerializableBarStrategy,
        bars=bars[:2],
    )
    assert prefix.resume_state is not None
    statistics = copy.deepcopy(prefix.resume_state.statistics_state)
    assert isinstance(statistics, dict)
    if corruption == "missing":
        statistics = {}
    elif corruption == "empty_equity":
        statistics["equity_curve"] = []
    else:
        equity_curve = statistics["equity_curve"]
        assert isinstance(equity_curve, list)
        equity_curve[0] = replace(equity_curve[0], bar_index=99)
    state = replace(prefix.resume_state, statistics_state=statistics)
    engine = BacktestEngine(_legacy_config(bars))

    with pytest.raises(ResumeUnsupportedError, match="statistics_state|equity_curve"):
        engine.run(SerializableBarStrategy, bars=bars, resume_state=state)

    assert engine.cash == engine.config.initial_capital
    assert engine.orders == []
    assert engine.closed_trades == []


def test_resume_preflights_runtime_restore_before_broker_mutation() -> None:
    bars, _ = _lifecycle_fixture()
    prefix = BacktestEngine(_legacy_config(bars)).run(
        SerializableBarStrategy,
        bars=bars[:2],
    )
    assert prefix.resume_state is not None
    assert isinstance(prefix.resume_state.broker_state, BrokerSnapshot)
    altered_broker = replace(prefix.resume_state.broker_state, cash=123.0)
    state = replace(prefix.resume_state, broker_state=altered_broker)
    config = _legacy_config(bars)
    assert config.runtime is not None
    config.runtime.restore_state = None  # type: ignore[method-assign]
    engine = BacktestEngine(config)

    with pytest.raises(ResumeUnsupportedError, match="runtime.*restore_state"):
        engine.run(SerializableBarStrategy, bars=bars, resume_state=state)

    assert engine.cash == engine.config.initial_capital
    assert engine.orders == []


def test_resume_checkpoint_is_detached_and_reusable() -> None:
    bars, ticks = _lifecycle_fixture()
    partial_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], partial_ticks, export_resume_state=True)
    partial_config.end_time = int(bars[-1].time_close or bars[-1].time)
    prefix = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy, bars=bars[:2]
    )
    assert prefix.resume_state is not None
    checkpoint_before = copy.deepcopy(prefix.resume_state)

    first = BacktestEngine(_config(bars, ticks)).run(
        TrailingAndReversalStrategy,
        bars=bars,
        resume_state=prefix.resume_state,
    )
    second = BacktestEngine(_config(bars, ticks)).run(
        TrailingAndReversalStrategy,
        bars=bars,
        resume_state=prefix.resume_state,
    )

    assert prefix.resume_state == checkpoint_before
    assert first.closed_trades == second.closed_trades
    assert first.open_trades == second.open_trades
    assert first.equity_curve == second.equity_curve
    assert first.content_hash_value == second.content_hash_value


def test_strict_resume_rejects_truncated_score_history_before_mutation() -> None:
    bars, ticks = _lifecycle_fixture()
    kwargs: dict[str, Any] = {
        "export_resume_state": True,
        "score_start_time": bars[1].time,
        "score_end_time": int(bars[-1].time_close or bars[-1].time),
        "content_hash_include_equity_curve": True,
    }
    partial_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], partial_ticks, **kwargs)
    partial_config.end_time = int(bars[-1].time_close or bars[-1].time)
    prefix = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy, bars=bars[:2]
    )
    assert prefix.resume_state is not None
    statistics = copy.deepcopy(prefix.resume_state.statistics_state)
    assert isinstance(statistics, dict)
    assert statistics["score_equity_points"]
    statistics["score_equity_points"] = []
    state = replace(prefix.resume_state, statistics_state=statistics)
    engine = BacktestEngine(_config(bars, ticks, **kwargs))

    with pytest.raises(ResumeUnsupportedError, match="score_equity_points"):
        engine.run(TrailingAndReversalStrategy, bars=bars, resume_state=state)

    assert engine.cash == engine.config.initial_capital
    assert engine.closed_trades == []


@pytest.mark.parametrize("restore_target", ["runtime_state", "strategy_state"])
def test_strict_restore_failure_is_typed_and_atomic(restore_target: str) -> None:
    bars, _ = _lifecycle_fixture()
    prefix = BacktestEngine(_legacy_config(bars)).run(
        SerializableBarStrategy, bars=bars[:2]
    )
    assert prefix.resume_state is not None
    assert isinstance(prefix.resume_state.broker_state, BrokerSnapshot)
    broken = replace(
        prefix.resume_state,
        broker_state=replace(prefix.resume_state.broker_state, cash=123.0),
        **{restore_target: {}},
    )
    config = _legacy_config(bars)
    runtime = config.runtime
    assert isinstance(runtime, SerializableBarRuntime)
    runtime_before = copy.deepcopy(runtime.export_state())
    engine = BacktestEngine(config)

    with pytest.raises(ResumeUnsupportedError, match=f"{restore_target}.*restore"):
        engine.run(SerializableBarStrategy, bars=bars, resume_state=broken)

    assert engine.cash == engine.config.initial_capital
    assert engine.orders == []
    assert runtime.export_state() == runtime_before


def test_event_inclusive_resume_matches_uninterrupted_identity() -> None:
    bars, ticks = _lifecycle_fixture()
    kwargs: dict[str, Any] = {
        "collect_events": True,
        "content_hash_include_events": True,
        "content_hash_include_equity_curve": True,
        "export_resume_state": True,
    }
    uninterrupted = BacktestEngine(_config(bars, ticks, **kwargs)).run(
        TrailingAndReversalStrategy, bars=bars
    )
    partial_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], partial_ticks, **kwargs)
    partial_config.end_time = int(bars[-1].time_close or bars[-1].time)
    prefix = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy, bars=bars[:2]
    )
    assert prefix.resume_state is not None

    resumed = BacktestEngine(_config(bars, ticks, **kwargs)).run(
        TrailingAndReversalStrategy,
        bars=bars,
        resume_state=prefix.resume_state,
    )

    assert resumed.events == uninterrupted.events
    assert resumed.warnings == uninterrupted.warnings
    assert resumed.errors == uninterrupted.errors
    assert resumed.content_hash_value == uninterrupted.content_hash_value


def test_fresh_tick_run_does_not_inherit_previous_equity_history() -> None:
    bars, ticks = _lifecycle_fixture()
    engine = BacktestEngine(
        _config(bars, ticks, content_hash_include_equity_curve=True)
    )

    first = engine.run(TrailingAndReversalStrategy, bars=bars)
    second = engine.run(TrailingAndReversalStrategy, bars=bars)

    assert [point.bar_index for point in second.equity_curve or []] == list(
        range(len(bars))
    )
    assert second.equity_curve == first.equity_curve
    assert second.content_hash_value == first.content_hash_value


def test_strict_resume_rejects_statistics_inconsistent_with_broker_trades() -> None:
    bars, ticks = _lifecycle_fixture()
    partial_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], partial_ticks, export_resume_state=True)
    partial_config.end_time = int(bars[-1].time_close or bars[-1].time)
    prefix = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy,
        bars=bars[:2],
    )
    state = prefix.resume_state
    assert state is not None
    assert isinstance(state.statistics_state, dict)
    assert state.statistics_state["engine_gross_profit_total"] == 1.0
    state.statistics_state["engine_gross_profit_total"] = 999.0

    with pytest.raises(
        ResumeUnsupportedError,
        match="engine_gross_profit_total.*broker_state.closed_trades",
    ):
        BacktestEngine(_config(bars, ticks)).run(
            TrailingAndReversalStrategy,
            bars=bars,
            resume_state=state,
        )


@pytest.mark.parametrize(
    "field",
    [
        "engine_closed_trade_stats_count",
        "engine_gross_profit_total",
        "engine_gross_loss_total",
        "engine_win_trades_total",
        "engine_loss_trades_total",
        "engine_even_trades_total",
    ],
)
def test_reused_tick_engine_rejects_statistics_before_runtime_restore(field: str) -> None:
    bars, ticks = _lifecycle_fixture()
    config = _config(bars, ticks)
    runtime = config.runtime
    assert isinstance(runtime, SyntheticRealtimeRuntime)
    engine = BacktestEngine(config)
    engine.run(TrailingAndReversalStrategy, bars=bars)

    partial_ticks = [tick for tick in ticks if tick.time < int(bars[1].time_close or 0)]
    partial_config = _config(bars[:2], partial_ticks, export_resume_state=True)
    partial_config.end_time = int(bars[-1].time_close or bars[-1].time)
    prefix = BacktestEngine(partial_config).run(
        TrailingAndReversalStrategy,
        bars=bars[:2],
    )
    assert prefix.resume_state is not None
    statistics = copy.deepcopy(prefix.resume_state.statistics_state)
    assert isinstance(statistics, dict)
    statistics[field] = statistics[field] + 1
    tampered = replace(prefix.resume_state, statistics_state=statistics)

    runtime_before = copy.deepcopy(runtime.export_state(include_varip=True))
    restore_calls = 0
    original_restore = runtime.restore_state

    def spy_restore(state: object) -> None:
        nonlocal restore_calls
        restore_calls += 1
        original_restore(state)

    runtime.restore_state = spy_restore  # type: ignore[method-assign]
    with pytest.raises(ResumeUnsupportedError, match=f"{field}.*closed_trades"):
        engine.run(
            TrailingAndReversalStrategy,
            bars=bars,
            resume_state=tampered,
        )

    assert restore_calls == 0
    assert runtime.export_state(include_varip=True) == runtime_before


def test_strict_broker_statistics_validate_losses_evens_and_trade_types() -> None:
    bars, ticks = _lifecycle_fixture()
    prefix = BacktestEngine(
        _config(bars, ticks, export_resume_state=True)
    ).run(TrailingAndReversalStrategy, bars=bars)
    state = prefix.resume_state
    assert state is not None
    assert isinstance(state.broker_state, BrokerSnapshot)
    assert state.broker_state.closed_trades
    template = state.broker_state.closed_trades[0]
    broker = replace(
        state.broker_state,
        closed_trades=[
            replace(template, profit=-2.0),
            replace(template, profit=0.0),
        ],
    )
    statistics = {
        "closed_trade_stats_count": 2,
        "engine_closed_trade_stats_count": 2,
        "engine_gross_profit_total": 0.0,
        "engine_gross_loss_total": 2.0,
        "engine_win_trades_total": 0,
        "engine_loss_trades_total": 1,
        "engine_even_trades_total": 1,
    }
    resume_state_module._validate_strict_statistics_against_broker(
        statistics,
        broker,
        score_start_index=0,
    )

    malformed = replace(
        state.broker_state,
        closed_trades=[object()],  # type: ignore[list-item]
    )
    with pytest.raises(ResumeUnsupportedError, match="must contain Trade"):
        resume_state_module._validate_strict_statistics_against_broker(
            statistics,
            malformed,
            score_start_index=0,
        )

    invalid_state = replace(
        state,
        broker_state=object(),  # type: ignore[arg-type]
    )
    with pytest.raises(ResumeUnsupportedError, match="must be a BrokerSnapshot"):
        resume_state_module.prevalidate_strict_resume_before_external_state(
            BacktestEngine(_config(bars, ticks)),
            invalid_state,
        )


def test_strict_external_restore_requires_rollback_exporter() -> None:
    with pytest.raises(ResumeUnsupportedError, match="export rollback state"):
        resume_state_module._restore_external_states(
            strict=True,
            runtime=object(),
            runtime_restore=lambda _state: None,
            runtime_state={},
            strategy=object(),
            strategy_restore=None,
            strategy_state=None,
        )


def test_external_restore_wraps_rollback_export_failure() -> None:
    class ExportFailure:
        def export_state(self) -> object:
            raise RuntimeError("export failed")

    target = ExportFailure()
    with pytest.raises(ResumeUnsupportedError, match="export failed before restore"):
        resume_state_module._restore_external_states(
            strict=True,
            runtime=target,
            runtime_restore=lambda _state: None,
            runtime_state={},
            strategy=object(),
            strategy_restore=None,
            strategy_state=None,
        )


def test_nonstrict_external_restore_without_rollback_is_typed() -> None:
    def fail(_state: object) -> None:
        raise RuntimeError("restore failed")

    with pytest.raises(ResumeUnsupportedError, match="runtime_state restore failed"):
        resume_state_module._restore_external_states(
            strict=False,
            runtime=object(),
            runtime_restore=fail,
            runtime_state={},
            strategy=object(),
            strategy_restore=None,
            strategy_state=None,
        )


def test_external_restore_reports_rollback_failure() -> None:
    class RestoreFailure:
        def export_state(self) -> object:
            return {"before": True}

        def restore_state(self, _state: object) -> None:
            raise RuntimeError("restore and rollback failed")

    target = RestoreFailure()
    with pytest.raises(ResumeUnsupportedError, match="rollback failed"):
        resume_state_module._restore_external_states(
            strict=True,
            runtime=target,
            runtime_restore=target.restore_state,
            runtime_state={"broken": True},
            strategy=object(),
            strategy_restore=None,
            strategy_state=None,
        )


def test_strict_resume_rejects_invalid_diagnostic_history() -> None:
    bars, _ = _lifecycle_fixture()
    prefix = BacktestEngine(_legacy_config(bars)).run(
        SerializableBarStrategy, bars=bars[:2]
    )
    assert prefix.resume_state is not None
    statistics = copy.deepcopy(prefix.resume_state.statistics_state)
    assert isinstance(statistics, dict)
    statistics["events"] = [object()]
    state = replace(prefix.resume_state, statistics_state=statistics)

    with pytest.raises(ResumeUnsupportedError, match="events must contain diagnostics"):
        BacktestEngine(_legacy_config(bars)).run(
            SerializableBarStrategy, bars=bars, resume_state=state
        )


@pytest.mark.parametrize(
    ("fail_on_call", "message"),
    [
        (1, "statistics_state could not be detached"),
        (2, "broker_state could not be detached"),
    ],
)
def test_strict_resume_wraps_detachment_failures(
    monkeypatch: pytest.MonkeyPatch, fail_on_call: int, message: str
) -> None:
    bars, _ = _lifecycle_fixture()
    prefix = BacktestEngine(_legacy_config(bars)).run(
        SerializableBarStrategy, bars=bars[:2]
    )
    assert prefix.resume_state is not None
    original_clone = resume_state_module.clone_state
    calls = 0

    def fail_selected_clone(value: object) -> object:
        nonlocal calls
        calls += 1
        if calls == fail_on_call:
            raise TypeError("cannot clone")
        return original_clone(value)

    monkeypatch.setattr(resume_state_module, "clone_state", fail_selected_clone)
    with pytest.raises(ResumeUnsupportedError, match=message):
        BacktestEngine(_legacy_config(bars)).run(
            SerializableBarStrategy, bars=bars, resume_state=prefix.resume_state
        )
