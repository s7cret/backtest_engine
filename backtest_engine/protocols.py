from __future__ import annotations

from typing import Any, Protocol, Sequence

from .models import BacktestResumeState, Bar, BarSeries, Fill, Order, Tick


class DataProvider(Protocol):
    def get_bars(
        self, symbol: str, timeframe: str, start_time: int, end_time: int
    ) -> Sequence[Bar] | BarSeries: ...
    def get_lower_tf_bars(
        self, symbol: str, parent_timeframe: str, lower_timeframe: str, parent_bar: Bar
    ) -> Sequence[Bar] | BarSeries: ...


class RealtimeTickProvider(Protocol):
    def get_ticks(
        self, symbol: str, timeframe: str, start_time: int, end_time: int
    ) -> Sequence[Tick]: ...


class PineRuntime(Protocol):
    def begin_bar(self, bar: Bar, bar_index: int) -> None: ...
    def end_bar(self) -> None: ...


class GeneratedStrategy(Protocol):
    ctx: object

    def _process_bar(self, bar: Bar, bar_index: int) -> None: ...


class ResultWriter(Protocol):
    def write(self, result: object, path: str) -> None: ...


class BrokerAdapter(Protocol):
    def on_order(self, order: Order) -> None: ...
    def on_fill(self, fill: Fill) -> None: ...


class SerializableRuntime(Protocol):
    def export_state(self) -> object: ...
    def restore_state(self, state: object) -> None: ...


class SerializableStrategy(Protocol):
    def export_state(self) -> object: ...
    def restore_state(self, state: object) -> None: ...


class ResumeStateSerializer(Protocol):
    serializer_id: str

    def dumps(self, state: object) -> bytes: ...
    def loads(self, payload: bytes) -> object: ...


class ResumeCapableEngine(Protocol):
    def run(
        self,
        strategy_class: type[Any],
        params: dict | None = None,
        bars: BarSeries | list[Bar] | None = None,
        callbacks: object | None = None,
        resume_state: BacktestResumeState | None = None,
    ) -> object: ...
