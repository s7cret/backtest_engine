from __future__ import annotations

import pytest

pytest.importorskip("pinelib")

from backtest_engine import BacktestConfig, BacktestEngine  # noqa: E402
from backtest_engine.adapters.generated_strategy import (  # noqa: E402
    UnsupportedGeneratedStrategySemantics,
    make_generated_strategy_adapter,
)
from backtest_engine.context import EntryOrderPayload, ExitPayload  # noqa: E402
from backtest_engine.context.strategy_context import StrategyContext  # noqa: E402
from backtest_engine.models import Bar  # noqa: E402


class GeneratedLikeStrategy:
    def __init__(self, params=None, runtime=None):
        self.params = params or {}
        self.rt = runtime
        self.ctx = None

    def _process_bar(self, bar):
        del bar
        idx = self.rt.bar_index_series.current
        if idx == 1:
            self.ctx.entry("L", "long")
        if idx == 4:
            self.ctx.close("L")


def test_generated_strategy_adapter_runs_orders_through_backtest_engine() -> None:
    strategy_class = make_generated_strategy_adapter(GeneratedLikeStrategy)
    bars = [Bar(i, 100 + i, 101 + i, 99 + i, 100 + i, 1.0) for i in range(6)]
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=5,
        commission_type="none",
        commission_value=0.0,
        default_qty_type="fixed",
        default_qty_value=1.0,
        force_close_on_end=False,
    )

    result = BacktestEngine(config).run(strategy_class, bars=bars)

    assert result.status == "completed"
    assert result.total_trades == 1
    assert result.net_profit == pytest.approx(3.0)
    assert result.closed_trades is not None
    assert result.closed_trades[0].entry_bar_index == 2
    assert result.closed_trades[0].exit_bar_index == 5


def test_generated_strategy_adapter_sets_request_data_end_ms() -> None:
    class GeneratedReadsRequestEnd:
        seen: list[int | None] = []

        def __init__(self, params=None, runtime=None):
            self.params = params or {}
            self.rt = runtime
            self.ctx = None

        def _process_bar(self, bar):
            del bar
            self.seen.append(self.rt.request_data_end_ms)

    strategy_class = make_generated_strategy_adapter(GeneratedReadsRequestEnd)
    strategy_class.runtime_request_data_end_ms = 12345
    bars = [Bar(i, 100 + i, 101 + i, 99 + i, 100 + i, 1.0) for i in range(2)]
    config = BacktestConfig(symbol="TEST", timeframe="1", start_time=0, end_time=1)

    result = BacktestEngine(config).run(strategy_class, bars=bars)

    assert result.status == "completed"
    assert GeneratedReadsRequestEnd.seen == [12345, 12345]


def test_generated_strategy_adapter_exposes_max_bars_back_to_pinelib_runtime() -> None:
    class GeneratedReadsRuntimeConfig:
        seen: list[int | None] = []

        def __init__(self, params=None, runtime=None):
            self.params = params or {}
            self.rt = runtime
            self.ctx = None

        def _process_bar(self, bar):
            del bar
            self.seen.append(self.rt.config.extra.get("max_bars_back"))

    strategy_class = make_generated_strategy_adapter(GeneratedReadsRuntimeConfig)
    bars = [Bar(i, 100 + i, 101 + i, 99 + i, 100 + i, 1.0) for i in range(2)]
    config = BacktestConfig(
        symbol="TEST", timeframe="1", start_time=0, end_time=1, max_bars_back=5000
    )

    result = BacktestEngine(config).run(strategy_class, bars=bars)

    assert result.status == "completed"
    assert GeneratedReadsRuntimeConfig.seen == [5000, 5000]


def test_strategy_context_buffers_typed_command_payloads() -> None:
    ctx = StrategyContext(
        BacktestConfig(symbol="TEST", timeframe="1", start_time=0, end_time=1)
    )

    ctx.entry("L", "long", qty=2.0)
    ctx.exit("XL", from_entry="L", profit=3.0)

    commands = ctx.buffer.drain()
    assert isinstance(commands[0].payload, EntryOrderPayload)
    assert commands[0].payload.id == "L"
    assert commands[0].payload.direction == "long"
    assert commands[0].kwargs["qty"] == 2.0
    assert isinstance(commands[1].payload, ExitPayload)
    assert commands[1].payload.from_entry == "L"
    assert commands[1].kwargs["profit"] == 3.0


def test_generated_strategy_adapter_preserves_score_window_phase_metrics() -> None:
    strategy_class = make_generated_strategy_adapter(GeneratedLikeStrategy)
    bars = [Bar(i, 100 + i, 101 + i, 99 + i, 100 + i, 1.0) for i in range(8)]
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=7,
        score_start_time=3,
        score_end_time=7,
        commission_type="none",
        commission_value=0.0,
        default_qty_type="fixed",
        default_qty_value=1.0,
        force_close_on_end=False,
    )

    result = BacktestEngine(config).run(strategy_class, bars=bars, effective_pre_bars=3)

    assert result.status == "completed"
    assert result.bars_processed == 5
    assert result.score_net_profit == pytest.approx(2.0)
    assert result.phase_trades is not None
    assert result.phase_trades[0].entry_phase == "prehistory"
    assert result.phase_trades[0].exit_phase == "score"
    assert result.phase_trades[0].crosses_score_boundary is True


class GeneratedClosedTradesChangeStrategy:
    events: list[tuple[int, float]] = []

    def __init__(self, params=None, runtime=None):
        from pinelib.ta import change

        self.params = params or {}
        self.rt = runtime
        self.ctx = None
        self._change = change

    def _process_bar(self, bar):
        del bar
        idx = self.rt.bar_index_series.current
        delta = self._change(self.ctx.closedtrades)
        if delta == 1:
            self.events.append((idx, delta))
        if idx == 1:
            self.ctx.entry("L", "long")
        if idx == 4:
            self.ctx.close("L")


def test_generated_strategy_adapter_tracks_strategy_scalar_history() -> None:
    GeneratedClosedTradesChangeStrategy.events = []
    strategy_class = make_generated_strategy_adapter(
        GeneratedClosedTradesChangeStrategy
    )
    bars = [Bar(i, 100 + i, 101 + i, 99 + i, 100 + i, 1.0) for i in range(7)]
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=6,
        commission_type="none",
        commission_value=0.0,
        default_qty_type="fixed",
        default_qty_value=1.0,
        force_close_on_end=False,
    )

    result = BacktestEngine(config).run(strategy_class, bars=bars)

    assert result.status == "completed"
    assert GeneratedClosedTradesChangeStrategy.events == [(5, 1.0)]


class GeneratedReadsTradeAccessors:
    open_seen: list[tuple[float, float, float, int]] = []
    closed_seen: list[tuple[float, float, float, float, float, int, int]] = []

    def __init__(self, params=None, runtime=None):
        self.params = params or {}
        self.rt = runtime
        self.ctx = None

    def _process_bar(self, bar):
        del bar
        idx = self.rt.bar_index_series.current
        if idx == 1:
            self.ctx.entry("L", "long", qty=2)
        if self.ctx.opentrades > 0:
            trade_idx = self.ctx.opentrades - 1
            self.open_seen.append(
                (
                    self.ctx.opentrades_entry_price(trade_idx),
                    self.ctx.opentrades_size(trade_idx),
                    self.ctx.opentrades_profit(trade_idx),
                    self.ctx.opentrades_entry_bar_index(trade_idx),
                )
            )
        if idx == 4:
            self.ctx.close("L")
        if self.ctx.closedtrades > 0:
            trade_idx = self.ctx.closedtrades - 1
            self.closed_seen.append(
                (
                    self.ctx.closedtrades_profit(trade_idx),
                    self.ctx.closedtrades_commission(trade_idx),
                    self.ctx.closedtrades_size(trade_idx),
                    self.ctx.closedtrades_entry_price(trade_idx),
                    self.ctx.closedtrades_exit_price(trade_idx),
                    self.ctx.closedtrades_entry_bar_index(trade_idx),
                    self.ctx.closedtrades_exit_bar_index(trade_idx),
                )
            )


def test_generated_strategy_adapter_exposes_trade_accessors() -> None:
    GeneratedReadsTradeAccessors.open_seen = []
    GeneratedReadsTradeAccessors.closed_seen = []
    strategy_class = make_generated_strategy_adapter(GeneratedReadsTradeAccessors)
    bars = [Bar(i, 100 + i, 101 + i, 99 + i, 100 + i, 1.0) for i in range(7)]
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=6,
        commission_type="none",
        commission_value=0.0,
        default_qty_type="fixed",
        default_qty_value=1.0,
        force_close_on_end=False,
    )

    result = BacktestEngine(config).run(strategy_class, bars=bars)

    assert result.status == "completed"
    assert GeneratedReadsTradeAccessors.open_seen[-1] == pytest.approx(
        (102.0, 2.0, 4.0, 2)
    )
    assert GeneratedReadsTradeAccessors.closed_seen[-1] == pytest.approx(
        (6.0, 0.0, 2.0, 102.0, 105.0, 2, 5)
    )


class GeneratedRecordsPlots:
    def __init__(self, params=None, runtime=None):
        self.params = params or {}
        self.rt = runtime
        self.ctx = None

    def _process_bar(self, bar):
        del bar
        self.rt.plot_recorder.record_plot(
            bar_time=self.rt.current_bar.time,
            bar_index=self.rt.bar_index,
            value=self.rt.close.current,
            title="close_plot",
        )


def test_generated_strategy_adapter_exports_plot_records() -> None:
    strategy_class = make_generated_strategy_adapter(GeneratedRecordsPlots)
    bars = [Bar(i, 100 + i, 101 + i, 99 + i, 100 + i, 1.0) for i in range(3)]
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=2,
        commission_type="none",
        commission_value=0.0,
    )

    result = BacktestEngine(config).run(strategy_class, bars=bars)

    assert result.status == "completed"
    assert result.plots == [
        (0, -1, 100, "close_plot"),
        (1000, 0, 101, "close_plot"),
        (2000, 1, 102, "close_plot"),
    ]
    assert "plots" in result.available_outputs


class _Declaration:
    calc_on_order_fills = True
    calc_on_every_tick = False
    use_bar_magnifier = False
    margin_long = 100.0
    margin_short = 100.0


class _GeneratedCtx:
    declaration = _Declaration()


class UnsupportedGeneratedLikeStrategy:
    def __init__(self, params=None, runtime=None):
        del params, runtime
        self.ctx = _GeneratedCtx()


def test_generated_strategy_adapter_fails_closed_for_unsupported_recalc_semantics() -> (
    None
):
    strategy_class = make_generated_strategy_adapter(UnsupportedGeneratedLikeStrategy)
    bars = [Bar(0, 1, 1, 1, 1, 1.0)]
    config = BacktestConfig(symbol="TEST", timeframe="1", start_time=0, end_time=0)

    with pytest.raises(
        UnsupportedGeneratedStrategySemantics, match="calc_on_order_fills"
    ):
        BacktestEngine(config).run(strategy_class, bars=bars)


class GeneratedCalcOnFillStrategy:
    def __init__(self, params=None, runtime=None):
        self.params = params or {}
        self.rt = runtime
        self.ctx = _GeneratedCtx()
        self.entered = False
        self.closed = False

    def _process_bar(self, bar):
        del bar
        idx = self.rt.bar_index_series.current
        if idx == 0 and not self.entered and self.ctx.position_size == 0:
            self.entered = True
            self.ctx.entry("L", "long")
        if idx == 1 and self.entered and not self.closed and self.ctx.position_size > 0:
            self.closed = True
            self.ctx.close("L")


def test_generated_strategy_adapter_supports_calc_on_order_fills_recalc_bridge() -> (
    None
):
    strategy_class = make_generated_strategy_adapter(GeneratedCalcOnFillStrategy)
    bars = [Bar(i, 100.0, 100.0, 100.0, 100.0, 1.0) for i in range(3)]
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=2,
        commission_type="none",
        commission_value=0.0,
        process_orders_on_close=False,
        calc_on_order_fills=True,
    )

    result = BacktestEngine(config).run(strategy_class, bars=bars)

    assert result.status == "completed"
    assert result.total_trades == 1
    assert len(result.closed_trades or []) == 1
    assert result.closed_trades[0].entry_bar_index == 1
    assert result.closed_trades[0].exit_bar_index == 1


class _MatchingDeclaration:
    initial_capital = 10000.0
    default_qty_type = "fixed"
    default_qty_value = 1.0
    pyramiding = 0
    commission_type = "none"
    commission_value = 0.0
    slippage = 0.0
    process_orders_on_close = False
    margin_long = 100.0
    margin_short = 100.0
    calc_on_order_fills = False
    calc_on_every_tick = False
    use_bar_magnifier = False


class _GeneratedCtxMatching:
    declaration = _MatchingDeclaration()


class GeneratedWithMatchingDeclaration(GeneratedLikeStrategy):
    def __init__(self, params=None, runtime=None):
        super().__init__(params=params, runtime=runtime)
        self.ctx = _GeneratedCtxMatching()


def test_generated_strategy_adapter_config_handshake_accepts_empty_diff() -> None:
    strategy_class = make_generated_strategy_adapter(GeneratedWithMatchingDeclaration)
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=1,
        commission_type="none",
        commission_value=0.0,
    )
    result = BacktestEngine(config).run(
        strategy_class, bars=[Bar(0, 1, 1, 1, 1), Bar(1, 1, 1, 1, 1)]
    )
    assert result.status == "completed"


def test_generated_strategy_adapter_config_handshake_rejects_mismatch() -> None:
    strategy_class = make_generated_strategy_adapter(GeneratedWithMatchingDeclaration)
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=1,
        initial_capital=20000.0,
        commission_type="none",
        commission_value=0.0,
    )
    with pytest.raises(UnsupportedGeneratedStrategySemantics, match="initial_capital"):
        BacktestEngine(config).run(
            strategy_class, bars=[Bar(0, 1, 1, 1, 1), Bar(1, 1, 1, 1, 1)]
        )


class GeneratedRecordsPineTime:
    seen_times: list[int] = []

    def __init__(self, params=None, runtime=None):
        self.params = params or {}
        self.rt = runtime
        self.ctx = None

    def _process_bar(self, bar):
        del bar
        self.__class__.seen_times.append(self.rt.time.current)


def test_generated_strategy_adapter_converts_second_timestamps_to_pine_milliseconds() -> (
    None
):
    GeneratedRecordsPineTime.seen_times = []
    strategy_class = make_generated_strategy_adapter(GeneratedRecordsPineTime)
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1D",
        start_time=1_000,
        end_time=1_000,
        commission_type="none",
        commission_value=0.0,
    )
    BacktestEngine(config).run(strategy_class, bars=[Bar(1_000, 1, 1, 1, 1)])

    assert GeneratedRecordsPineTime.seen_times == [1_000_000]


class GeneratedProcessOrdersOnCloseStrategy:
    def __init__(self, params=None, runtime=None):
        self.params = params or {}
        self.rt = runtime
        self.ctx = None

    def _process_bar(self, bar):
        del bar
        if self.rt.bar_index_series.current == 0:
            self.ctx.entry("L", "long", qty=1)


def test_generated_strategy_adapter_covers_process_orders_on_close_entry() -> None:
    strategy_class = make_generated_strategy_adapter(
        GeneratedProcessOrdersOnCloseStrategy
    )
    bars = [
        Bar(0, 10, 10, 10, 10, 1.0),
        Bar(1, 20, 20, 20, 20, 1.0),
    ]
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=1,
        commission_type="none",
        commission_value=0.0,
        default_qty_type="fixed",
        default_qty_value=1.0,
        process_orders_on_close=True,
    )

    result = BacktestEngine(config).run(strategy_class, bars=bars)

    assert result.status == "completed"
    assert result.open_trades[0].entry_bar_index == 0
    assert result.open_trades[0].entry_price == pytest.approx(10.0)


class GeneratedProfitLossExitStrategy:
    def __init__(self, params=None, runtime=None):
        self.params = params or {}
        self.rt = runtime
        self.ctx = None

    def _process_bar(self, bar):
        del bar
        idx = self.rt.bar_index_series.current
        if idx == 0:
            self.ctx.entry("L", "long", qty=1)
        if idx == 1:
            self.ctx.exit("XP", from_entry="L", qty=1, profit=3, loss=2)


def test_generated_strategy_adapter_covers_profit_loss_exit() -> None:
    strategy_class = make_generated_strategy_adapter(GeneratedProfitLossExitStrategy)
    bars = [
        Bar(0, 10, 10, 10, 10, 1.0),
        Bar(1, 10, 10, 10, 10, 1.0),
        Bar(2, 10, 13, 9, 12, 1.0),
    ]
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=2,
        commission_type="none",
        commission_value=0.0,
        default_qty_type="fixed",
        default_qty_value=1.0,
        mintick=1.0,
    )

    result = BacktestEngine(config).run(strategy_class, bars=bars)

    assert result.status == "completed"
    assert result.total_trades == 1
    assert result.closed_trades[0].exit_id == "XP:L"
    assert result.closed_trades[0].exit_price == pytest.approx(13.0)
    assert result.net_profit == pytest.approx(3.0)


class GeneratedTrailingExitStrategy:
    def __init__(self, params=None, runtime=None):
        self.params = params or {}
        self.rt = runtime
        self.ctx = None

    def _process_bar(self, bar):
        del bar
        idx = self.rt.bar_index_series.current
        if idx == 0:
            self.ctx.entry("L", "long", qty=1)
        if idx == 1:
            self.ctx.exit("TR", from_entry="L", qty=1, trail_price=12, trail_offset=1)


def test_generated_strategy_adapter_covers_trailing_exit() -> None:
    strategy_class = make_generated_strategy_adapter(GeneratedTrailingExitStrategy)
    bars = [
        Bar(0, 10, 10, 10, 10, 1.0),
        Bar(1, 10, 10, 10, 10, 1.0),
        Bar(2, 10, 13, 10, 12, 1.0),
    ]
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=2,
        commission_type="none",
        commission_value=0.0,
        default_qty_type="fixed",
        default_qty_value=1.0,
        mintick=1.0,
    )

    result = BacktestEngine(config).run(strategy_class, bars=bars)

    assert result.status == "completed"
    assert result.total_trades == 1
    assert result.closed_trades[0].exit_id == "TR:T"
    assert result.closed_trades[0].exit_price == pytest.approx(12.0)
    assert result.net_profit == pytest.approx(2.0)


class GeneratedCloseAllStrategy:
    def __init__(self, params=None, runtime=None):
        self.params = params or {}
        self.rt = runtime
        self.ctx = None

    def _process_bar(self, bar):
        del bar
        idx = self.rt.bar_index_series.current
        if idx == 0:
            self.ctx.entry("L1", "long", qty=1)
            self.ctx.entry("L2", "long", qty=1)
        if idx == 1:
            self.ctx.close_all(immediately=True)


def test_generated_strategy_adapter_covers_close_all_immediately() -> None:
    strategy_class = make_generated_strategy_adapter(GeneratedCloseAllStrategy)
    bars = [
        Bar(0, 10, 10, 10, 10, 1.0),
        Bar(1, 11, 11, 11, 11, 1.0),
        Bar(2, 12, 12, 12, 12, 1.0),
    ]
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=2,
        commission_type="none",
        commission_value=0.0,
        default_qty_type="fixed",
        default_qty_value=1.0,
        pyramiding=2,
    )

    result = BacktestEngine(config).run(strategy_class, bars=bars)

    assert result.status == "completed"
    assert result.total_trades == 2
    assert [trade.exit_id for trade in result.closed_trades] == [
        "close_all",
        "close_all",
    ]
    assert result.net_profit == pytest.approx(0.0)


class GeneratedCloseThenOppositeEntryStrategy:
    def __init__(self, params=None, runtime=None):
        self.params = params or {}
        self.rt = runtime
        self.ctx = None

    def _process_bar(self, bar):
        del bar
        idx = self.rt.bar_index_series.current
        if idx == 0:
            self.ctx.entry("L", "long")
        if idx == 2:
            self.ctx.close("L")
            self.ctx.entry("S", "short")


def test_generated_strategy_close_then_opposite_entry_does_not_double_reversal_qty() -> (
    None
):
    strategy_class = make_generated_strategy_adapter(
        GeneratedCloseThenOppositeEntryStrategy
    )
    bars = [
        Bar(0, 100, 100, 100, 100, 1.0),
        Bar(1, 100, 100, 100, 100, 1.0),
        Bar(2, 100, 100, 100, 100, 1.0),
        Bar(3, 100, 100, 100, 100, 1.0),
        Bar(4, 100, 100, 100, 100, 1.0),
    ]
    config = BacktestConfig(
        symbol="TEST",
        timeframe="1",
        start_time=0,
        end_time=4,
        commission_type="none",
        commission_value=0.0,
        default_qty_type="percent_of_equity",
        default_qty_value=10.0,
        initial_capital=10000.0,
        force_close_on_end=False,
    )

    result = BacktestEngine(config).run(strategy_class, bars=bars)

    assert result.status == "completed"
    assert result.total_trades == 1
    assert result.open_trades is not None
    assert len(result.open_trades) == 1
    assert result.open_trades[0].direction == "short"
    assert result.open_trades[0].qty == pytest.approx(10.0)
