from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Sequence
from typing import Literal, Mapping

from backtest_engine.models import Bar, BarSeries, Tick
from backtest_engine.errors import ConfigError
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
