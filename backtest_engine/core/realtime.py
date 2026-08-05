from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping

from backtest_engine.core.deterministic_hash import sha256_obj
from backtest_engine.models import Bar, BarSeries, Tick
from backtest_engine.errors import ConfigError, TickReplayDataError
from backtest_engine.core.state_snapshot import RealtimeExecutionCheckpoint


@dataclass(frozen=True, slots=True)
class BarTickSlice:
    """Ticks deterministically assigned to a parent historical/realtime bar.

    This is scheduler groundwork only. It does not execute Pine strategy code and
    does not implement TradingView realtime rollback/commit semantics.
    """

    bar_index: int
    bar: Bar
    ticks: tuple[Tick, ...]


@dataclass(frozen=True, slots=True)
class RealtimeTickAttempt:
    """One guarded realtime tick attempt checkpoint.

    This records rollback scaffolding only. It is not evidence that Pine code,
    broker fills, or TradingView realtime semantics were executed.
    """

    bar_index: int
    tick_index: int
    tick: Tick
    checkpoint: RealtimeExecutionCheckpoint
    rolled_back: bool = True
    strategy_invoked: bool = False
    policy: Literal["discard", "commit_final"] = "discard"
    committed: bool = False


@dataclass(frozen=True, slots=True)
class RealtimeTickCommitPolicy:
    """Policy boundary for future realtime tick side-effect commits.

    Current production execution stays fail-closed. The only supported policy is
    to discard every tick attempt unless tests explicitly request a final-tick
    commit through guarded skeleton APIs.
    """

    commit_final_tick: bool = False
    allow_intrabar_order_fills: bool = False
    intrabar_order_fill_oracle_proof: Mapping[str, object] | None = None

    def action_for(
        self, tick_index: int, total_ticks: int
    ) -> Literal["discard", "commit_final"]:
        if self.commit_final_tick and total_ticks > 0 and tick_index == total_ticks - 1:
            return "commit_final"
        return "discard"


@dataclass(frozen=True, slots=True)
class RealtimeOrderFillOracleStatus:
    """Machine-readable gate for realtime broker/order-fill evidence.

    ``blocked`` and ``partial`` are non-production states. ``proven`` is
    reserved for future sanitized TradingView Strategy Tester oracle evidence;
    the engine does not currently ship such a proof.
    """

    status: Literal["blocked", "partial", "proven"] = "blocked"
    evidence_artifact: str | None = None
    strategy_tester_rows_proven: bool = False
    intrabar_order_fill_semantics_proven: bool = False
    tick_completeness_proven: bool = False

    def as_proof(self) -> dict[str, object]:
        return {
            "status": self.status,
            "evidence_artifact": self.evidence_artifact,
            "strategy_tester_rows_proven": self.strategy_tester_rows_proven,
            "intrabar_order_fill_semantics_proven": self.intrabar_order_fill_semantics_proven,
            "tick_completeness_proven": self.tick_completeness_proven,
        }


def validate_realtime_order_fill_oracle_proof(
    proof: Mapping[str, object] | None,
) -> None:
    """Fail closed unless a future proof explicitly satisfies every gate."""

    if proof is None:
        raise ConfigError(
            "realtime intrabar order/fill commits require explicit TradingView tick oracle proof"
        )
    if proof.get("status") != "proven":
        raise ConfigError("TradingView intrabar order/fill oracle proof is not proven")
    required_true = (
        "strategy_tester_rows_proven",
        "intrabar_order_fill_semantics_proven",
        "tick_completeness_proven",
    )
    missing = [key for key in required_true if proof.get(key) is not True]
    if missing:
        raise ConfigError(
            "TradingView intrabar order/fill oracle proof is incomplete: "
            + ", ".join(missing)
        )


@dataclass(frozen=True, slots=True)
class RuntimeTickUpdate:
    """Duck-typed tick payload for runtimes with update_realtime_tick()."""

    price: float
    volume: float = 0.0
    time: int | None = None
    is_final: bool = False


def _as_ticks(ticks: Iterable[Tick]) -> list[Tick]:
    out = list(ticks)
    for index, tick in enumerate(out):
        if not isinstance(tick, Tick):
            raise ConfigError(
                f"realtime_ticks[{index}] must be a Tick instance"
            )
        if isinstance(tick.time, bool) or not isinstance(tick.time, int):
            raise ConfigError(f"realtime_ticks[{index}].time must be an integer")
        for field_name in ("price", "volume", "bid", "ask"):
            value = getattr(tick, field_name)
            if value is None:
                if field_name == "price":
                    raise ConfigError(
                        f"realtime_ticks[{index}].price must be finite"
                    )
                continue
            canonical = _canonical_price(value)
            if canonical is None:
                raise ConfigError(
                    f"realtime_ticks[{index}].{field_name} must be finite"
                )
            if field_name == "volume" and canonical < 0:
                raise ConfigError(
                    f"realtime_ticks[{index}].volume must be non-negative"
                )
        if tick.bid is not None and tick.ask is not None:
            bid = _canonical_price(tick.bid)
            ask = _canonical_price(tick.ask)
            if bid is not None and ask is not None and bid > ask:
                raise ConfigError(
                    f"realtime_ticks[{index}] bid must be less than or equal to ask"
                )
    for prev, cur in zip(out, out[1:], strict=False):
        if cur.time < prev.time:
            raise ConfigError("realtime_ticks must be sorted by non-decreasing time")
    return out


def build_bar_tick_schedule(
    bars: BarSeries | Sequence[Bar], ticks: Iterable[Tick]
) -> tuple[BarTickSlice, ...]:
    """Map realtime ticks onto parent bars using `[bar.time, bar.time_close)` windows.

    If a bar lacks `time_close`, the next bar's open time is used. For the final
    bar without `time_close`, ticks at or after `bar.time` are assigned to that
    final bar. Ticks before the first bar or in gaps between explicit windows are
    rejected rather than silently dropped.
    """

    series = bars if isinstance(bars, BarSeries) else BarSeries.from_bars(bars)
    tick_list = _as_ticks(ticks)
    slices: list[BarTickSlice] = []
    tick_i = 0
    n_ticks = len(tick_list)
    n_bars = len(series)

    for bar_i in range(n_bars):
        bar = series.get_bar(bar_i)
        if bar.time_close is not None:
            end: int | None = int(bar.time_close)
        elif bar_i + 1 < n_bars:
            end = int(series.time[bar_i + 1])
        else:
            end = None

        if end is not None and end < bar.time:
            raise ConfigError(
                "bar time_close must be greater than or equal to bar time"
            )

        assigned: list[Tick] = []
        while tick_i < n_ticks:
            tick = tick_list[tick_i]
            if tick.time < bar.time:
                raise ConfigError(
                    "realtime_ticks contain a tick before the current bar window"
                )
            if end is not None and tick.time >= end:
                break
            assigned.append(tick)
            tick_i += 1
        slices.append(BarTickSlice(bar_i, bar, tuple(assigned)))

    if tick_i < n_ticks:
        raise ConfigError("realtime_ticks contain ticks outside available bar windows")
    return tuple(slices)


def resolve_realtime_tick_schedule(
    config: Any, bars: BarSeries | Sequence[Bar]
) -> tuple[BarTickSlice, ...]:
    """Resolve explicit ticks and prove they reconstruct every parent OHLC bar."""

    series = bars if isinstance(bars, BarSeries) else BarSeries.from_bars(bars)
    source = config.realtime_ticks
    if source is None:
        provider = config.realtime_tick_provider
        get_ticks = getattr(provider, "get_ticks", None)
        if not callable(get_ticks):
            raise TickReplayDataError(
                "realtime_tick_provider must implement get_ticks(symbol, timeframe, start, end)"
            )
        if not len(series):
            source = ()
        else:
            last = series.get_bar(len(series) - 1)
            end = int(last.time_close if last.time_close is not None else last.time)
            try:
                source = get_ticks(
                    config.symbol, config.timeframe, int(series.time[0]), end
                )
            except Exception as exc:
                raise TickReplayDataError(
                    f"realtime_tick_provider.get_ticks failed: {type(exc).__name__}: {exc}"
                ) from exc
    try:
        schedule = build_bar_tick_schedule(series, source)
    except TypeError as exc:
        raise TickReplayDataError("realtime tick source must be an iterable of Tick") from exc
    for tick_slice in schedule:
        _validate_tick_slice_reconstructs_bar(tick_slice)
    return schedule


def realtime_tick_schedule_payload(
    schedule: Sequence[BarTickSlice],
) -> list[dict[str, Any]]:
    """Return the canonical, wall-clock-free execution identity for a schedule."""

    return [
        {
            "parent_bar_index": tick_slice.bar_index,
            "parent_time": tick_slice.bar.time,
            "parent_time_close": tick_slice.bar.time_close,
            "ticks": [
                {
                    "tick_index": tick_index,
                    "time": tick.time,
                    "price": tick.price,
                    "volume": tick.volume,
                    "bid": tick.bid,
                    "ask": tick.ask,
                }
                for tick_index, tick in enumerate(tick_slice.ticks)
            ],
        }
        for tick_slice in schedule
    ]


def realtime_tick_schedule_fingerprint(schedule: Sequence[BarTickSlice]) -> str:
    """Hash every execution-relevant tick field, ordering, and parent mapping."""

    return sha256_obj(realtime_tick_schedule_payload(schedule))


def _canonical_price(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _same_price(left: float, right: float) -> bool:
    canonical_left = _canonical_price(left)
    canonical_right = _canonical_price(right)
    return canonical_left is not None and canonical_left == canonical_right


def _validate_tick_slice_reconstructs_bar(tick_slice: BarTickSlice) -> None:
    ticks = tick_slice.ticks
    bar = tick_slice.bar
    if not ticks:
        raise TickReplayDataError(
            f"realtime ticks do not reconstruct parent OHLC for bar {tick_slice.bar_index}: no ticks"
        )
    prices = [tick.price for tick in ticks]
    actual = (prices[0], max(prices), min(prices), prices[-1])
    expected = (bar.open, bar.high, bar.low, bar.close)
    if not all(_same_price(a, e) for a, e in zip(actual, expected, strict=True)):
        raise TickReplayDataError(
            "realtime ticks do not reconstruct parent OHLC for bar "
            f"{tick_slice.bar_index}: expected={expected!r}, actual={actual!r}"
        )
    expected_volume = _canonical_price(bar.volume)
    if expected_volume is None or expected_volume < 0:
        raise TickReplayDataError(
            "realtime ticks cannot reconstruct parent OHLCV for bar "
            f"{tick_slice.bar_index}: parent volume must be finite and non-negative"
        )
    actual_volume = Decimal(0)
    for tick_index, tick in enumerate(ticks):
        tick_volume = _canonical_price(tick.volume)
        if tick_volume is None or tick_volume < 0:
            raise TickReplayDataError(
                "realtime ticks cannot reconstruct parent OHLCV for bar "
                f"{tick_slice.bar_index}: tick {tick_index} volume is missing or invalid"
            )
        actual_volume += tick_volume
    if actual_volume != expected_volume:
        raise TickReplayDataError(
            "realtime ticks do not reconstruct parent OHLCV volume for bar "
            f"{tick_slice.bar_index}: expected={expected_volume}, actual={actual_volume}"
        )
