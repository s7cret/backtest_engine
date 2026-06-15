from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest

from backtest_engine.adapters import generated_strategy as bridge
from backtest_engine.config import BacktestConfig
from backtest_engine.models import Bar


@dataclass
class FakeBarState:
    islast: bool = False
    ishistory: bool = True
    isrealtime: bool = False
    isnew: bool = True
    isconfirmed: bool = True


class FakePlotRecorder:
    def __init__(self) -> None:
        self.window: tuple[int | None, int | None] | None = None

    def set_time_window(self, start: int | None, end: int | None) -> None:
        self.window = (start, end)


class FakeRuntimeConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.extra = kwargs.get("extra", {})
        self.diagnostics: list[dict[str, Any]] = []


class FakeSymbolInfo:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class FakeTimeframeInfo:
    def __init__(self, value: str) -> None:
        self.value = value
        self.interval_ms = 60_000 if value == "1" else None

    @classmethod
    def from_string(cls, value: str) -> "FakeTimeframeInfo":
        return cls(value)


class FakePineBar:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class FakePineRuntime:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)
        self.config = kwargs.get("config") or FakeRuntimeConfig()
        self.timeframe = kwargs.get("timeframe") or FakeTimeframeInfo("1")
        self.plot_recorder = FakePlotRecorder()
        self.request_data_end_ms: int | None = None
        self.barstate = FakeBarState()
        self.strategy: Any | None = None
        self.bar_index = -1
        self.bars: list[FakePineBar] = []
        self.end_count = 0

    def begin_bar(self, bar: FakePineBar) -> None:
        self.bars.append(bar)
        self.bar_index += 1

    def end_bar(self) -> None:
        self.end_count += 1


class FakeNA:
    pass


FAKE_NA = FakeNA()


def install_fake_pinelib(monkeypatch: pytest.MonkeyPatch) -> None:
    pinelib = types.ModuleType("pinelib")
    core = types.ModuleType("pinelib.core")
    core.PineRuntime = FakePineRuntime
    core.Bar = FakePineBar
    core.na = FAKE_NA
    types_mod = types.ModuleType("pinelib.core.types")
    types_mod.RuntimeConfig = FakeRuntimeConfig
    types_mod.SymbolInfo = FakeSymbolInfo
    types_mod.TimeframeInfo = FakeTimeframeInfo
    na_mod = types.ModuleType("pinelib.core.na")
    na_mod.is_na = lambda value: value is FAKE_NA
    monkeypatch.setitem(sys.modules, "pinelib", pinelib)
    bar_mod = types.ModuleType("pinelib.core.bar")
    bar_mod.Bar = FakePineBar
    monkeypatch.setitem(sys.modules, "pinelib.core", core)
    monkeypatch.setitem(sys.modules, "pinelib.core.bar", bar_mod)
    monkeypatch.setitem(sys.modules, "pinelib.core.types", types_mod)
    monkeypatch.setitem(sys.modules, "pinelib.core.na", na_mod)


class FakeTradeState:
    equity = 10_100.0
    net_profit = 100.0
    open_profit = 3.0
    gross_profit = 120.0
    gross_loss = 20.0
    position_size = 2.0
    position_avg_price = None
    open_trades = 1
    closed_trades = 1
    max_drawdown = 5.0
    max_runup = 150.0
    win_trades = 1
    loss_trades = 0
    even_trades = 0

    def closedtrades_max_runup(self, index: int) -> float:
        return 10.0 + index

    def closedtrades_max_drawdown(self, index: int) -> float:
        return 2.0 + index

    def closedtrades_entry_id(self, index: int) -> str:
        return f"L{index}"

    def closedtrades_exit_id(self, index: int) -> str:
        return f"XL{index}"

    def closedtrades_entry_price(self, index: int) -> float:
        return 100.0 + index

    def closedtrades_exit_price(self, index: int) -> float:
        return 105.0 + index

    def closedtrades_entry_time(self, index: int) -> int:
        return 1000 + index

    def closedtrades_exit_time(self, index: int) -> int:
        return 2000 + index

    def closedtrades_commission(self, index: int) -> float:
        return 1.5 + index

    def closedtrades_size(self, index: int) -> float:
        return 2.0 + index

    def closedtrades_qty(self, index: int) -> float:
        return 2.0 + index

    def closedtrades_side(self, index: int) -> str:
        return "long"

    def closedtrades_profit(self, index: int) -> float:
        return 8.0 + index

    def closedtrades_profit_percent(self, index: int) -> float:
        return 4.0 + index

    def closedtrades_entry_bar_index(self, index: int) -> int:
        return index

    def closedtrades_exit_bar_index(self, index: int) -> int:
        return index + 3

    def opentrades_max_runup(self, index: int) -> float:
        return 6.0 + index

    def opentrades_max_drawdown(self, index: int) -> float:
        return 1.0 + index

    def opentrades_entry_id(self, index: int) -> str:
        return f"O{index}"

    def opentrades_entry_price(self, index: int) -> float:
        return 99.0 + index

    def opentrades_entry_time(self, index: int) -> int:
        return 900 + index

    def opentrades_entry_bar_index(self, index: int) -> int:
        return index + 1

    def opentrades_commission(self, index: int) -> float:
        return 0.5 + index

    def opentrades_size(self, index: int) -> float:
        return 1.0 + index

    def opentrades_qty(self, index: int) -> float:
        return 1.0 + index

    def opentrades_side(self, index: int) -> str:
        return "short"

    def opentrades_profit(self, index: int) -> float:
        return -3.0 + index

    def opentrades_profit_percent(self, index: int) -> float:
        return -1.0 + index


class FakeEngineContext:
    def __init__(self) -> None:
        self.config = BacktestConfig(
            symbol="TEST", timeframe="1", start_time=0, end_time=1
        )
        self.state = FakeTradeState()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))

    def entry(self, **kwargs: Any) -> None:
        self._record("entry", **kwargs)

    def order(self, **kwargs: Any) -> None:
        self._record("order", **kwargs)

    def exit(self, **kwargs: Any) -> None:
        self._record("exit", **kwargs)

    def close(self, **kwargs: Any) -> None:
        self._record("close", **kwargs)

    def close_all(self, **kwargs: Any) -> None:
        self._record("close_all", **kwargs)

    def cancel(self, *args: Any, **kwargs: Any) -> None:
        self._record("cancel", id=args[0], **kwargs)

    def cancel_all(self) -> None:
        self._record("cancel_all")

    def risk_allow_entry_in(self, direction: str) -> None:
        self._record("risk_allow_entry_in", direction=direction)

    def risk_max_drawdown(self, value: float, type: str) -> None:
        self._record("risk_max_drawdown", value=value, type=type)

    def risk_max_position_size(self, value: float, type: str = "fixed") -> None:
        self._record("risk_max_position_size", value=value, type=type)

    def risk_max_intraday_loss(self, value: float, type: str) -> None:
        self._record("risk_max_intraday_loss", value=value, type=type)

    def risk_max_intraday_filled_orders(
        self, value: float, type: str = "fixed"
    ) -> None:
        self._record("risk_max_intraday_filled_orders", value=value, type=type)


def test_bridge_scalar_series_history_math_and_na(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_pinelib(monkeypatch)
    series = bridge._BridgeScalarSeries(10)
    assert series.current == 10
    series.commit_current()
    series.set_current(12)
    assert series[0] == 12
    assert series[1] == 10
    assert series[9] == 0
    with pytest.raises(IndexError):
        _ = series[-1]
    assert float(series) == 12.0
    assert int(series) == 12
    assert bool(series)
    assert series + 3 == 15
    assert 3 + series == 15
    assert series - 2 == 10
    assert 20 - series == 8
    assert series * 2 == 24
    assert 2 * series == 24
    assert series / 3 == 4
    assert 36 / series == 3
    assert series == bridge._BridgeScalarSeries(12)
    assert series >= 11 and series > 11 and series <= 12 and series < 13
    na_series = bridge._BridgeScalarSeries(FAKE_NA)
    assert na_series + 1 is FAKE_NA
    assert 1 + na_series is FAKE_NA
    assert na_series - 1 is FAKE_NA
    assert 1 - na_series is FAKE_NA
    assert na_series * 2 is FAKE_NA
    assert 2 * na_series is FAKE_NA
    assert na_series / 2 is FAKE_NA
    assert 2 / na_series is FAKE_NA
    assert bridge._none_if_pine_na(FAKE_NA) is None


def test_bridge_context_forwards_ledger_order_and_risk_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_pinelib(monkeypatch)
    engine_ctx = FakeEngineContext()
    ctx = bridge._BridgeStrategyContext(engine_ctx)  # type: ignore[arg-type]
    runtime = FakePineRuntime()
    ctx.attach_runtime(runtime)
    assert runtime.strategy is ctx
    assert ctx.position_avg_price.current is FAKE_NA
    assert ctx.closedtrades_entry_id(0) == "L0"
    assert ctx.closedtrades_exit_id(0) == "XL0"
    assert ctx.closedtrades_entry_price(0) == 100.0
    assert ctx.closedtrades_exit_price(0) == 105.0
    assert ctx.closedtrades_entry_time(0) == 1000
    assert ctx.closedtrades_exit_time(0) == 2000
    assert ctx.closedtrades_commission(0) == 1.5
    assert ctx.closedtrades_size(0) == 2.0
    assert ctx.closedtrades_qty(0) == 2.0
    assert ctx.closedtrades_side(0) == "long"
    assert ctx.closedtrades_profit(0) == 8.0
    assert ctx.closedtrades_profit_percent(0) == 4.0
    assert ctx.closedtrades_max_runup(0) == 10.0
    assert ctx.closedtrades_max_drawdown(0) == 2.0
    assert ctx.closedtrades_entry_bar_index(0) == 0
    assert ctx.closedtrades_exit_bar_index(0) == 3
    assert ctx.opentrades_entry_id(0) == "O0"
    assert ctx.opentrades_entry_price(0) == 99.0
    assert ctx.opentrades_entry_time(0) == 900
    assert ctx.opentrades_entry_bar_index(0) == 1
    assert ctx.opentrades_commission(0) == 0.5
    assert ctx.opentrades_size(0) == 1.0
    assert ctx.opentrades_qty(0) == 1.0
    assert ctx.opentrades_side(0) == "short"
    assert ctx.opentrades_profit(0) == -3.0
    assert ctx.opentrades_profit_percent(0) == -1.0
    assert ctx.opentrades_max_runup(0) == 6.0
    assert ctx.opentrades_max_drawdown(0) == 1.0
    ctx.entry("L", "long", qty=FAKE_NA, limit=FAKE_NA, stop=1.0, comment="e")
    ctx.order(
        "S",
        "short",
        qty=1.0,
        limit=FAKE_NA,
        stop=FAKE_NA,
        oca_name="g",
        oca_type="cancel",
        comment="o",
    )
    ctx.exit(
        "XL",
        from_entry="L",
        qty=FAKE_NA,
        qty_percent=50,
        limit=FAKE_NA,
        stop=99,
        profit=FAKE_NA,
        loss=1,
        trail_price=FAKE_NA,
        trail_points=2,
        trail_offset=FAKE_NA,
        oca_name="x",
        oca_type="reduce",
        comment="x",
    )
    ctx.close("L", qty=FAKE_NA, qty_percent=100, immediately=True, comment="c")
    ctx.close_all(immediately=True, comment="ca")
    ctx.cancel("L")
    ctx.cancel_all()
    ctx.risk_allow_entry_in("long")
    ctx.risk_max_drawdown(10, "percent_of_equity")
    ctx.risk_max_position_size(5)
    ctx.risk_max_intraday_loss(3, "cash")
    ctx.risk_max_intraday_filled_orders(2)
    ctx.accept_orders_from_generated_code()
    assert [name for name, _ in engine_ctx.calls] == [
        "entry",
        "order",
        "exit",
        "close",
        "close_all",
        "cancel",
        "cancel_all",
        "risk_allow_entry_in",
        "risk_max_drawdown",
        "risk_max_position_size",
        "risk_max_intraday_loss",
        "risk_max_intraday_filled_orders",
    ]
    assert engine_ctx.calls[0][1]["qty"] is None
    ctx._commit_scalar_history()
    assert ctx.equity.committed_length == 1


def test_generated_strategy_adapter_lifecycle_and_declaration_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_pinelib(monkeypatch)

    class Declaration:
        initial_capital = 10000.0
        default_qty_type = "fixed"
        default_qty_value = 1.0
        pyramiding = 0
        commission_type = "percent"
        commission_value = 0.055
        slippage = 0.0
        process_orders_on_close = False
        close_entries_rule = "fifo"
        margin_long = 100.0
        margin_short = 100.0
        calc_on_order_fills = False
        calc_on_every_tick = False
        use_bar_magnifier = False

    class Generated:
        seen: list[tuple[int, Any]] = []

        def __init__(
            self, params: dict[str, Any] | None = None, runtime: Any | None = None
        ) -> None:
            self.params = params or {}
            self.rt = runtime
            self.ctx = types.SimpleNamespace(declaration=Declaration())

        def _process_bar(self, bar: Any) -> None:
            self.seen.append((self.rt.bar_index, bar.time))

    adapter_cls = bridge.make_generated_strategy_adapter(Generated)
    adapter_cls.runtime_capture_plots = False
    adapter_cls.runtime_request_data_end_ms = 123
    adapter = adapter_cls(params={"a": 1}, ctx=FakeEngineContext())
    assert adapter._pine_runtime.plot_recorder.window == (1, 0)
    assert adapter._pine_runtime.request_data_end_ms == 123
    adapter._process_bar(Bar(1, 1, 2, 0, 1, None, 60_999), 1)
    adapter._process_bar(Bar(61_000, 1, 2, 0, 1, 2.0, 120_999), 2)
    adapter._finalize()
    assert adapter._pine_runtime.end_count == 2
    assert Generated.seen == [(0, 1000), (1, 61_000_000)]
    assert adapter._pine_runtime.bars[0].volume == 0.0
    assert adapter._pine_runtime.bars[0].time_close == 60_999_000

    class NoProcess(Generated):
        _process_bar = None

    bad_adapter = bridge.make_generated_strategy_adapter(NoProcess)(
        ctx=FakeEngineContext()
    )
    with pytest.raises(bridge.GeneratedStrategyBridgeError, match="_process_bar"):
        bad_adapter._process_bar(Bar(0, 1, 1, 1, 1), 1)

    class BadDeclaration(Declaration):
        calc_on_order_fills = True

    with pytest.raises(
        bridge.UnsupportedGeneratedStrategySemantics, match="calc_on_order_fills"
    ):
        adapter_cls._validate_generated_declaration(
            types.SimpleNamespace(declaration=BadDeclaration()),
            bridge.GeneratedStrategyAdapterOptions(),
            FakeEngineContext().config,
        )


@pytest.mark.xfail(
    reason=(
        "Negative scenario: relies on pinelib.core being NOT installed.  "
        "4.0 ships with pinelib 4.0 installed editable in the dev venv, so "
        "sys.modules['pinelib.core'] survives any monkeypatch; the error "
        "path is unrunnable.  Re-enable if/when a venv without pinelib is "
        "available, or convert to a direct unit test against the import "
        "guard with mock.patch('sys.modules', {...})."
    ),
    strict=False,
)
def test_bridge_helper_errors_and_config_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.modules.pop("pinelib.core", None)
    sys.modules.pop("pinelib.core.types", None)
    with pytest.raises(
        bridge.GeneratedStrategyBridgeError, match="PineLib is required"
    ):
        bridge._make_pine_runtime(bridge.GeneratedStrategyAdapterOptions())
    with pytest.raises(
        bridge.GeneratedStrategyBridgeError, match="PineLib is required"
    ):
        bridge._pine_na()
    with pytest.raises(
        bridge.GeneratedStrategyBridgeError, match="PineLib is required"
    ):
        bridge._to_pine_bar(Bar(0, 1, 1, 1, 1))
    assert bridge._direction("long") == "long"
    with pytest.raises(bridge.UnsupportedGeneratedStrategySemantics, match="direction"):
        bridge._direction("flat")
    assert bridge._pine_timestamp(None) is None
    assert bridge._pine_timestamp(100) == 100_000
    assert bridge._pine_timestamp(10_000_000_000) == 10_000_000_000
    assert (
        bridge._normalize_pine_time_close(None, open_time=0, fixed_timeframe_ms=60_000)
        is None
    )
    assert (
        bridge._normalize_pine_time_close(
            59_999, open_time=0, fixed_timeframe_ms=60_000
        )
        == 60_000
    )
    assert (
        bridge._normalize_pine_time_close(
            60_000, open_time=0, fixed_timeframe_ms=60_000
        )
        == 60_000
    )
    decl = types.SimpleNamespace(
        initial_capital=999.0, commission_type="cash_per_order"
    )
    cfg = types.SimpleNamespace(
        initial_capital=1000.0, commission_type="fixed_per_order"
    )
    diff = bridge._declaration_config_diff(decl, cfg)
    assert diff == {"initial_capital": {"declaration": 999.0, "config": 1000.0}}
