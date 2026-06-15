from __future__ import annotations

import json
import types
from dataclasses import dataclass, replace

import pytest

from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.adapters.generated_strategy import (
    GeneratedStrategyAdapterOptions,
    GeneratedStrategyBridgeError,
    UnsupportedGeneratedStrategySemantics,
    _declaration_config_diff,
    make_generated_strategy_adapter,
)
from backtest_engine.adapters.generated_strategy_context import _is_pine_na
from backtest_engine.batch.shared_data import SharedBarCache
from backtest_engine.context import CommandBuffer, StrategyContext
from backtest_engine.context.strategy_context import RiskRule
from backtest_engine.core.fill_scanner import (
    _fill_price_for_order,
    _stop_fill_price,
    process_bar_fills,
    update_trailing_order,
)
from backtest_engine.core.risk_rules import (
    apply_risk_rules,
    max_position_size_allows,
    pending_entry_position_delta,
)
from backtest_engine.core.strategy_command_processor import (
    _add_or_modify_exit_order,
    _pending_full_close_for_current_position,
    flush_strategy_commands,
)
from backtest_engine.context.strategy_state_view import StrategyStateView
from backtest_engine.core.clock import BacktestClock
from backtest_engine.core.engine_validation import validate_backtest_config
from backtest_engine.core.margin_call import maybe_margin_call
from backtest_engine.core.oca import apply_oca
from backtest_engine.core.price_path import (
    price_path,
    validate_supplied_bar_magnifier_bars,
)
from backtest_engine.core.resume_state import restore_resume_state
from backtest_engine.core.state_snapshot import _plain, build_resume_state
from backtest_engine.errors import (
    BarMagnifierUnavailableError,
    ResumeUnsupportedError,
    UnsupportedRiskRuleError,
)
from backtest_engine.execution_backends.pine_runtime import (
    _bar_to_pinelib,
    _sync_strategy_context_from_config,
)
from backtest_engine.models import (
    BacktestResumeState,
    Bar,
    BarSeries,
    Diagnostic,
    Order,
    Position,
    Trade,
)
from backtest_engine.models.events import Event
from backtest_engine.models.window import ExecutionWindow, PrehistoryPlan, WarmupQuality
from backtest_engine.reporting.console import render as render_console
from backtest_engine.reporting.monte_carlo_report import (
    render as render_monte_carlo_report,
)
from backtest_engine.results import BacktestResult
from backtest_engine.results.comparison import ComparisonReport
from backtest_engine.results.drawdown import max_drawdown_from_curve
from backtest_engine.results.equity_curve import final_equity, summarize_equity_curve
from backtest_engine.results.metrics import summary_metrics, trade_profits
from backtest_engine.results.monte_carlo import bootstrap_trade_profits
from backtest_engine.results.parity import ParityTolerance
from backtest_engine.results.trade_log import closed_trade_rows, trade_to_row


def _bar(t: int = 0) -> Bar:
    return Bar(
        time=t,
        open=10.0,
        high=12.0,
        low=8.0,
        close=11.0,
        volume=1.0,
        time_close=t + 59_999,
    )


def _order(
    order_id: str = "L",
    *,
    kind: str = "entry",
    status: str = "active",
    oca_name: str | None = None,
    oca_type: str = "none",
    qty: float = 1.0,
) -> Order:
    return Order(
        id=order_id,
        kind=kind,  # type: ignore[arg-type]
        direction="long",
        side="buy",
        position_effect="open",
        order_type="market",
        qty=qty,
        created_bar_index=0,
        created_time=0,
        active_from_bar_index=0,
        position_direction="long",
        oca_name=oca_name,
        oca_type=oca_type,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
    )


def _trade(**overrides: object) -> Trade:
    data = dict(
        id="t",
        entry_id="L",
        exit_id="X",
        direction="long",
        entry_time=0,
        entry_bar_index=0,
        entry_price=10.0,
        exit_time=1,
        exit_bar_index=1,
        exit_price=12.0,
        qty=1.0,
        commission_entry=0.1,
        commission_exit=0.2,
        profit=2.0,
        profit_percent=20.0,
        max_runup=3.0,
        max_drawdown=1.0,
    )
    data.update(overrides)
    return Trade(**data)  # type: ignore[arg-type]


def test_small_public_utility_branches() -> None:
    cache = SharedBarCache()
    series = cache.put("a", [{"time": 1, "open": 1, "high": 2, "low": 0, "close": 1}])
    assert cache.get("a") is series
    assert cache.get_or_put("a", []) is series
    assert "a" in cache and list(cache) == ["a"] and len(cache) == 1
    cache.clear()
    assert len(cache) == 0

    assert Event is Diagnostic
    assert render_console(None) == ""
    assert render_console("ok") == "ok\n"
    assert bootstrap_trade_profits([], initial_capital=100, runs=10) == []
    assert bootstrap_trade_profits([1, -1], initial_capital=100, runs=0) == []
    assert ParityTolerance(qty=0.01).qty_equal(1.0, 1.005)


def test_command_buffer_and_strategy_context_edge_commands() -> None:
    buffer = CommandBuffer()
    buffer.add("close_all")
    buffer.add("cancel_all")
    commands = buffer.drain()
    assert commands[0].kwargs == {
        "qty": None,
        "qty_percent": None,
        "immediately": False,
        "comment": None,
    }
    assert commands[1].kwargs == {}
    with pytest.raises(ValueError, match="unsupported strategy command"):
        buffer.add("unknown")

    ctx = StrategyContext(
        BacktestConfig(symbol="S", timeframe="1", start_time=0, end_time=1)
    )
    ctx.cancel_all()
    ctx.risk_allow_entry_in("both")
    ctx.risk_max_drawdown(10, "cash")
    assert [rule.name for rule in ctx.drain_risk_rules()] == [
        "allow_entry_in",
        "max_drawdown",
    ]
    with pytest.raises(ValueError, match="unsupported risk_allow_entry_in"):
        ctx.risk_allow_entry_in("sideways")
    with pytest.raises(ValueError, match="unsupported risk_max_drawdown"):
        ctx.risk_max_drawdown(1, "shares")
    with pytest.raises(ValueError, match="unsupported risk_max_position_size"):
        ctx.risk_max_position_size(1, "cash")
    with pytest.raises(UnsupportedRiskRuleError):
        ctx.risk_max_intraday_loss(1, "cash")
    with pytest.raises(UnsupportedRiskRuleError):
        ctx.risk_max_intraday_filled_orders(1)


def test_state_view_unavailable_closed_trade_extremes_and_nonzero_open_extremes() -> (
    None
):
    open_trade = _trade(
        exit_id=None,
        exit_time=None,
        exit_bar_index=None,
        exit_price=None,
        max_runup=5.0,
        max_drawdown=2.0,
        is_open=True,
    )
    closed_trade = _trade(max_runup=None, max_drawdown=None)
    view = StrategyStateView(
        open_trades=1,
        closed_trades=1,
        _open_trades_ref=[open_trade],
        _closed_trades_ref=[closed_trade],
    )
    assert view.opentrades_max_runup(0) == 5.0
    assert view.opentrades_max_drawdown(0) == 2.0
    with pytest.raises(AttributeError, match="max_runup"):
        view.closedtrades_max_runup(0)
    with pytest.raises(AttributeError, match="max_drawdown"):
        view.closedtrades_max_drawdown(0)


def test_window_dataclass_validation_and_unknown_warmup_classification() -> None:
    kwargs = dict(
        requested_start_ms=0,
        requested_end_ms=10,
        score_start_ms=1,
        score_end_ms=9,
        provider_fetch_start_ms=0,
        provider_fetch_end_ms=10,
        pre_bars_count=0,
        score_bars_count=1,
    )
    with pytest.raises(ValueError, match="provider_fetch_end_ms"):
        ExecutionWindow(**{**kwargs, "provider_fetch_end_ms": 8})
    with pytest.raises(ValueError, match="score_start_ms"):
        ExecutionWindow(**{**kwargs, "score_start_ms": 9, "score_end_ms": 1})
    with pytest.raises(ValueError, match="requested_start_ms"):
        ExecutionWindow(**{**kwargs, "requested_start_ms": 10, "requested_end_ms": 0})
    with pytest.raises(ValueError, match="bar counts"):
        ExecutionWindow(**{**kwargs, "pre_bars_count": -1})
    with pytest.raises(ValueError, match="min_pre_bars"):
        PrehistoryPlan(1, 1, 1, min_pre_bars=-1)
    quality = WarmupQuality.classify(
        recommended_pre_bars_raw=10,
        requested_max_pre_bars=20,
        effective_pre_bars=5,
        actual_pre_bars=5,
    )
    assert quality.warmup_confidence == "unknown"


def test_generated_strategy_bridge_declaration_validation_edges() -> None:
    cls = make_generated_strategy_adapter(
        type(
            "Generated", (), {"__init__": lambda self, params=None, runtime=None: None}
        )
    )
    with pytest.raises(
        GeneratedStrategyBridgeError, match="StrategyContext is required"
    ):
        cls()

    @dataclass
    class Decl:
        calc_on_every_tick: bool = False
        calc_on_order_fills: bool = False
        use_bar_magnifier: bool = False
        margin_long: float = 100.0
        margin_short: float = 100.0
        initial_capital: float = 10000.0
        commission_type: str = "cash_per_order"

    opts = GeneratedStrategyAdapterOptions(fail_on_config_mismatch=False)
    config = BacktestConfig(
        symbol="S",
        timeframe="1",
        start_time=0,
        end_time=1,
        commission_type="fixed_per_order",
    )
    cls._validate_generated_declaration(
        types.SimpleNamespace(declaration=None), opts, config
    )
    with pytest.raises(
        UnsupportedGeneratedStrategySemantics, match="calc_on_every_tick"
    ):
        cls._validate_generated_declaration(
            types.SimpleNamespace(declaration=Decl(calc_on_every_tick=True)),
            opts,
            config,
        )
    with pytest.raises(UnsupportedGeneratedStrategySemantics, match="bar_magnifier"):
        cls._validate_generated_declaration(
            types.SimpleNamespace(declaration=Decl(use_bar_magnifier=True)),
            opts,
            config,
        )
    with pytest.raises(UnsupportedGeneratedStrategySemantics, match="non-standard"):
        cls._validate_generated_declaration(
            types.SimpleNamespace(declaration=Decl(margin_long=50)), opts, config
        )
    diff = _declaration_config_diff(Decl(initial_capital=123), config)
    assert diff["initial_capital"]["declaration"] == 123
    assert "commission_type" not in _declaration_config_diff(Decl(), config)


def test_validation_mutates_config_for_required_outputs_and_metrics() -> None:
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1",
        start_time=0,
        end_time=1,
        collect_equity_curve=False,
        collect_events=False,
        collect_mfe_mae=False,
        collect_trade_details=False,
        required_outputs={"equity_curve", "order_events", "mfe_mae"},
        required_metrics={"sharpe"},
    )
    validate_backtest_config(cfg)
    assert cfg.collect_equity_curve is True
    assert cfg.collect_events is True
    assert cfg.collect_mfe_mae is True
    assert cfg.collect_trade_details is True


def test_clock_resume_snapshot_and_pinelib_helpers_without_optional_dependency() -> (
    None
):
    clock = BacktestClock()
    clock.advance(_bar(10), 3)
    with pytest.raises(ValueError, match="backwards"):
        clock.advance(_bar(5), 2)

    assert _plain({"b": {2, 1}}) == {"b": [1, 2]}
    assert build_resume_state(bar_index=-1, config_snapshot_hash="h").bar_index == -1
    engine = types.SimpleNamespace(
        config=BacktestConfig(symbol="S", timeframe="1", start_time=0, end_time=1)
    )
    with pytest.raises(ResumeUnsupportedError, match="missing broker_state"):
        restore_resume_state(
            engine,
            BacktestResumeState(-1, "h"),
            object(),
            object(),
            StrategyContext(engine.config),
        )

    class Ctx:
        initial_capital = None
        declaration = types.SimpleNamespace(initial_capital=None)

    ctx = Ctx()
    _sync_strategy_context_from_config(
        ctx,
        BacktestConfig(
            symbol="S", timeframe="1", start_time=0, end_time=1, initial_capital=42
        ),
    )
    assert ctx.initial_capital == 42 and ctx.declaration.initial_capital == 42

    class PineBar:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    converted = _bar_to_pinelib(
        _bar(0), PineBar, fixed_timeframe_ms=60_000, normalize_time_close_exclusive=True
    )
    assert converted.time_close == 60_000
    assert converted.volume == 1.0
    # Pine v5/v6 semantics: `na` is the canonical NaN, so any NaN
    # value (including float('nan')) is recognized as a Pine `na`.
    # Pre-4.0 the helper treated NaN as a regular float; the 4.0 line
    # aligns with `math.isnan` and Pine Script's `na` sentinel.
    assert _is_pine_na(float("nan")) is True


def test_price_path_bar_magnifier_edges_and_margin_oca_helpers() -> None:
    cfg = BacktestConfig(
        symbol="S",
        timeframe="1",
        start_time=0,
        end_time=60_000,
        use_bar_magnifier=True,
        bar_magnifier_lower_tf="1",
        bar_magnifier_bars={},
    )
    engine = types.SimpleNamespace(config=cfg)
    with pytest.raises(
        BarMagnifierUnavailableError, match="empty lower timeframe bars"
    ):
        price_path(engine, _bar(0))
    lower = [Bar(time=0, open=1, high=0.5, low=1, close=1, time_close=59_999)]
    cfg.bar_magnifier_bars = {0: lower}
    with pytest.raises(BarMagnifierUnavailableError, match="invalid OHLC high"):
        validate_supplied_bar_magnifier_bars(engine, BarSeries.from_bars([_bar(0)]))

    order = _order("a", oca_name="grp", oca_type="reduce", qty=1)
    other = _order("b", oca_name="grp", qty=3)
    events: list[str] = []
    oca_engine = types.SimpleNamespace(
        orders=[order, other],
        _cb=lambda *a: None,
        _event=lambda code, *a: events.append(code),
    )
    apply_oca(oca_engine, order, _bar(), 0)
    assert other.qty == 2 and events == ["ORDER_MODIFIED"]

    margin_engine = types.SimpleNamespace(
        position=Position(size=1.0, avg_price=10.0, direction="long"),
        config=BacktestConfig(
            symbol="S", timeframe="1", start_time=0, end_time=1, margin_long=0.0
        ),
    )
    assert maybe_margin_call(margin_engine, 10, _bar(), 0, "open") is False


def test_result_and_reporting_remaining_branches() -> None:
    assert summarize_equity_curve([], default_equity=5, default_cash=7).final_cash == 7
    assert final_equity([], default=12) == 12
    assert max_drawdown_from_curve([]) is None
    assert (
        summary_metrics(profits=[1.0], initial_capital=100.0, final_equity=101.0)[
            "net_profit"
        ]
        == 1.0
    )
    assert trade_profits([_trade(profit=4.0)]) == [4.0]
    assert trade_to_row({"a": 1}) == {"a": 1}
    assert closed_trade_rows(types.SimpleNamespace(closed_trades=[{"a": 1}])) == [
        {"a": 1}
    ]
    assert "value" in render_monte_carlo_report([object()], format="text")
    assert json.loads(render_monte_carlo_report({"a": 1})) == {"a": 1}
    report = ComparisonReport(
        matched=True,
        our_count=1,
        reference_count=1,
        first_mismatch_index=None,
        summary={},
        diagnostics=[],
    )
    assert report.to_dict()["matched"] is True
    result = BacktestResult(
        equity_curve=[types.SimpleNamespace(x=1)],
        events=[Diagnostic("X", "m", "warning")],
    )
    assert result.content_hash(include_equity_curve=False, include_events=True)


class _FakeEngine:
    def __init__(self) -> None:
        self.config = BacktestConfig(
            symbol="S",
            timeframe="1",
            start_time=0,
            end_time=1,
            process_orders_on_close=True,
            max_position_size=1,
        )
        self.position = Position(size=1.0, avg_price=10.0, direction="long")
        self.orders: list[Order] = []
        self.open_trades = [
            _trade(
                entry_id="L",
                qty=1.0,
                is_open=True,
                exit_id=None,
                exit_time=None,
                exit_bar_index=None,
                exit_price=None,
            )
        ]
        self.closed_trades: list[Trade] = []
        self.diags: list[tuple[str, str | None]] = []
        self.events: list[str] = []
        self._filled_exit_entry_keys = None
        self._max_position_size: float | None = 1.0

    def _apply_risk_rules(self, ctx: StrategyContext) -> None:
        for rule in ctx.drain_risk_rules():
            if rule.name == "max_position_size":
                self._max_position_size = rule.value

    def _cb(self, *args: object) -> None:
        return None

    def _event(self, code: str, *args: object) -> None:
        self.events.append(code)

    def _diag(
        self, code: str, message: str, severity: str = "warning", *args: object
    ) -> None:
        self.diags.append((code, args[-1] if args else None))

    def _qty_from_args(
        self, args: dict[str, float | None], position_size: float | None, price: float
    ) -> float:
        del position_size, price
        return float(args.get("qty") or 0.0)

    def _matching_open_trades(self, from_entry: str | None) -> list[Trade]:
        return [
            t
            for t in self.open_trades
            if from_entry is None or t.entry_id == from_entry
        ]

    def _available_exit_qty(
        self, from_entry: str | None, exclude_order: Order | None = None
    ) -> float:
        del exclude_order
        return sum(t.qty for t in self._matching_open_trades(from_entry))

    def _exit_base_price(self, from_entry: str | None) -> float:
        trades = self._matching_open_trades(from_entry)
        return trades[0].entry_price if trades else 10.0

    def _add_order(self, order: Order, bar: Bar, index: int) -> None:
        del bar, index
        order.status = "active"
        self.orders.append(order)

    def _risk_allows_order(
        self, order: Order, bar: Bar, index: int, exclude_order: Order | None = None
    ) -> bool:
        del order, bar, index, exclude_order
        return True

    def _entry_direction_allowed(self, direction: str) -> bool:
        return direction == "long"

    def _entry_allowed(self, direction: str) -> bool:
        return True


def test_strategy_command_processor_cancel_close_exit_and_modify_edges() -> None:
    bar = _bar(10)
    engine = _FakeEngine()
    engine.orders = [
        _order("a", status="active"),
        _order("b", status="pending"),
        _order("c", status="filled"),
    ]
    ctx = StrategyContext(engine.config)
    ctx.cancel_all()
    flush_strategy_commands(engine, ctx, bar, 0)
    assert [o.status for o in engine.orders[:2]] == ["cancelled", "cancelled"]
    assert engine.events == ["ORDER_CANCELLED", "ORDER_CANCELLED"]

    engine = _FakeEngine()
    engine.position = Position()
    ctx = StrategyContext(engine.config)
    ctx.close("missing")
    flush_strategy_commands(engine, ctx, bar, 0)
    assert engine.orders == []

    engine = _FakeEngine()
    ctx = StrategyContext(engine.config)
    ctx.close("missing")
    flush_strategy_commands(engine, ctx, bar, 0)
    assert engine.diags[-1][0] == "ORDER_REJECTED_NO_MATCHING_ENTRY"

    engine = _FakeEngine()
    ctx = StrategyContext(engine.config)
    ctx.exit("X")
    flush_strategy_commands(engine, ctx, bar, 0)
    assert engine.diags[-1][0] == "ORDER_REJECTED_EMPTY_EXIT"

    engine = _FakeEngine()
    engine.open_trades = []
    ctx = StrategyContext(engine.config)
    ctx.exit("X", from_entry="L", limit=12)
    flush_strategy_commands(engine, ctx, bar, 0)
    assert engine.diags[-1][0] == "ORDER_REJECTED_NO_AVAILABLE_POSITION_QTY"

    engine = _FakeEngine()
    engine.closed_trades = [
        _trade(exit_id="X:L", entry_id="L", entry_time=0, entry_bar_index=0)
    ]
    ctx = StrategyContext(engine.config)
    ctx.exit("X", from_entry="L", limit=12)
    flush_strategy_commands(engine, ctx, bar, 0)
    assert engine.orders == []

    engine = _FakeEngine()
    existing = _order("XL", kind="exit", qty=1.0)
    existing.parent_exit_id = "X"
    engine.orders = [existing]
    _add_or_modify_exit_order(engine, _order("XL", kind="exit", qty=0.0), bar, 0)
    assert engine.diags[-1][0] == "ORDER_REJECTED_ZERO_QTY"

    class RiskFalseEngine(_FakeEngine):
        def _risk_allows_order(
            self, order: Order, bar: Bar, index: int, exclude_order: Order | None = None
        ) -> bool:
            return False

    engine2 = RiskFalseEngine()
    existing2 = _order("XL", kind="exit", qty=1.0)
    engine2.orders = [existing2]
    _add_or_modify_exit_order(engine2, _order("XL", kind="exit", qty=2.0), bar, 0)
    assert existing2.qty == 1.0

    engine = _FakeEngine()
    existing_entry = _order("L", kind="entry", qty=1.0)
    engine.orders = [existing_entry]
    ctx = StrategyContext(engine.config)
    ctx.entry("L", "long", qty=2.0, limit=10.5)
    flush_strategy_commands(engine, ctx, bar, 0)
    assert existing_entry.qty == 2.0
    assert existing_entry.limit_price == 10.5
    assert "ORDER_MODIFIED" in engine.events


def test_backtest_engine_internal_helper_edges() -> None:
    config = BacktestConfig(
        symbol="S",
        timeframe="1",
        start_time=100,
        end_time=200,
        min_qty=2,
        default_qty_type="fixed",
        default_qty_value=1,
        commission_type="none",
    )
    engine = BacktestEngine(config)
    assert len(engine._slice_range(BarSeries.from_bars([Bar(0, 1, 1, 1, 1)]))) == 0
    assert engine._qty_from_args({}, None, 10.0) == 0.0
    with pytest.raises(Exception, match="boom"):
        engine._call_strategy(
            types.SimpleNamespace(
                _process_bar=lambda bar, i: (_ for _ in ()).throw(RuntimeError("boom"))
            ),
            _bar(),
            0,
        )

    engine = BacktestEngine(
        BacktestConfig(
            symbol="S",
            timeframe="1",
            start_time=0,
            end_time=1,
            max_position_size=1,
            commission_type="none",
        )
    )
    engine._max_position_size = 1
    oversized = _order("big", qty=2.0)
    assert engine._risk_allows_order(oversized, _bar(), 0) is False
    zero = _order("zero", qty=0.0)
    engine._add_order(zero, _bar(), 0)
    assert any(d.code == "ORDER_REJECTED_ZERO_QTY" for d in engine.warnings)


def test_resume_state_non_strict_and_restore_requirements() -> None:
    config = BacktestConfig(
        symbol="S",
        timeframe="1",
        start_time=0,
        end_time=1,
        resume_validation_policy="diagnostic",
    )
    engine = BacktestEngine(config)
    state = engine._export_resume_state(
        0,
        strategy=object(),
        runtime=types.SimpleNamespace(export_state=lambda: {"r": 1}),
    )
    state = replace(state, config_snapshot_hash="mismatch", strategy_state={"s": 1})
    ctx = StrategyContext(config)
    with pytest.raises(ResumeUnsupportedError, match="strategy does not implement"):
        restore_resume_state(
            engine,
            state,
            object(),
            types.SimpleNamespace(restore_state=lambda x: None),
            ctx,
        )
    assert any(d.code == "RESUME_CONFIG_MISMATCH" for d in engine.warnings)

    state2 = BacktestResumeState(
        0,
        engine._config_hash(),
        broker_state=object(),
        runtime_state=None,
        strategy_state=None,
    )
    with pytest.raises(ResumeUnsupportedError, match="BrokerSnapshot"):
        restore_resume_state(engine, state2, object(), object(), ctx)


def test_risk_rules_all_branches_and_position_projection() -> None:
    engine = types.SimpleNamespace(
        _allow_long=False, _allow_short=False, _early_stop_enabled=False
    )
    ctx = StrategyContext(
        BacktestConfig(symbol="S", timeframe="1", start_time=0, end_time=1)
    )
    ctx.risk_allow_entry_in("all")
    ctx.risk_max_drawdown(5, "percent")
    ctx.risk_max_position_size(2)
    apply_risk_rules(engine, ctx)
    assert engine._allow_long is True and engine._allow_short is True
    assert engine._max_drawdown_stop_percent == 5
    assert engine._max_position_size == 2

    bad_ctx = StrategyContext(
        BacktestConfig(symbol="S", timeframe="1", start_time=0, end_time=1)
    )
    bad_ctx.risk_rules.append(RiskRule("unknown"))
    with pytest.raises(UnsupportedRiskRuleError):
        apply_risk_rules(engine, bad_ctx)

    a = _order("a", qty=1)
    b = _order("b", qty=2)
    b.direction = "short"
    assert pending_entry_position_delta([a, b], exclude_order=a) == -2
    assert max_position_size_allows(
        max_position_size=None, current_size=0, orders=[], order=a
    )
    exit_order = _order("x", kind="exit")
    assert max_position_size_allows(
        max_position_size=1, current_size=10, orders=[], order=exit_order
    )


class _FillEngine:
    def __init__(self) -> None:
        self.config = BacktestConfig(
            symbol="S",
            timeframe="1",
            start_time=0,
            end_time=1,
            collect_order_lifecycle=False,
            calc_on_order_fills=True,
            max_recalc_depth=0,
            commission_type="none",
        )
        self.orders: list[Order] = []
        self.filled: list[tuple[str, float, str]] = []
        self.diags: list[str] = []
        self.events: list[str] = []
        self.margin_calls_remaining = 0

    def _price_path(self, bar: Bar) -> list[tuple[float, str]]:
        return [(bar.open, "open"), (bar.high, "high"), (bar.close, "close")]

    def _fill(
        self, order: Order, bar: Bar, index: int, price: float, point: str
    ) -> None:
        del bar, index
        self.filled.append((order.id, price, point))
        order.status = "filled"

    def _maybe_margin_call(
        self, price: float, bar: Bar, index: int, point: str
    ) -> bool:
        del price, bar, index, point
        if self.margin_calls_remaining > 0:
            self.margin_calls_remaining -= 1
            return True
        return False

    def _update_open_profit(self, price: float) -> None:
        self.last_profit_price = price

    def _update_state(self) -> None:
        self.state_updated = True

    def _diag(self, code: str, *args: object) -> None:
        self.diags.append(code)

    def _call_strategy(self, strategy: object, bar: Bar, index: int) -> None:
        self.strategy_called = True

    def _flush(
        self,
        ctx: StrategyContext,
        bar: Bar,
        index: int,
        recalc_after_fill: bool = False,
    ) -> None:
        self.flush_called = recalc_after_fill

    def _event(self, code: str, *args: object) -> None:
        self.events.append(code)

    def _limit_fill_price(
        self, order: Order, price: float, is_open_point: bool
    ) -> float:
        del order, is_open_point
        return price

    def _matching_open_trades(self, from_entry: str | None) -> list[Trade]:
        return [] if from_entry else [_trade()]


def test_fill_scanner_remaining_price_and_recalc_branches() -> None:
    short_trail = _order("trail")
    short_trail.direction = "short"
    short_trail.trail_price = 10.0
    short_trail.trail_offset = 1.0
    update_trailing_order(short_trail, 9.0)
    assert short_trail.trail_activated is True
    assert short_trail.stop_price == 10.0

    engine = _FillEngine()
    engine.orders = [_order(str(i), status="filled") for i in range(33)] + [
        _order("market")
    ]
    process_bar_fills(
        engine, object(), StrategyContext(engine.config), _bar(), 1, open_only=True
    )
    assert len(engine.orders) == 1
    assert engine.diags == ["MAX_RECALC_DEPTH_REACHED"]

    engine2 = _FillEngine()
    engine2.margin_calls_remaining = 1
    engine2.orders = []
    process_bar_fills(engine2, object(), StrategyContext(engine2.config), _bar(), 1)
    assert engine2.diags == ["MAX_RECALC_DEPTH_REACHED"]

    engine3 = _FillEngine()
    pending_trailing = _order("pending", status="pending")
    pending_trailing.trail_price = 9.0
    pending_trailing.trail_offset = 0.5
    engine3.orders = [pending_trailing]
    process_bar_fills(
        engine3,
        object(),
        StrategyContext(engine3.config),
        _bar(),
        0,
        trailing_only=True,
    )
    assert pending_trailing.trail_activated is True

    exit_order = _order("exit", kind="exit")
    exit_order.from_entry = "missing"
    assert (
        _fill_price_for_order(_FillEngine(), exit_order, _bar(), 0, 10, "open", True)
        is None
    )

    stop_order = _order("stop")
    stop_order.order_type = "stop"
    stop_order.stop_price = None
    assert (
        _fill_price_for_order(_FillEngine(), stop_order, _bar(), 0, 10, "open", True)
        is None
    )

    stop_limit = _order("sl")
    stop_limit.order_type = "stop_limit"
    stop_limit.stop_price = 9.0
    stop_limit.limit_price = 7.0
    engine4 = _FillEngine()
    assert (
        _fill_price_for_order(engine4, stop_limit, _bar(), 0, 9.5, "high", False)
        is None
    )
    assert stop_limit.stop_limit_activated is True
    assert "STOP_LIMIT_ACTIVATED" in engine4.events

    stop_price_policy = _FillEngine()
    stop_price_policy.config.stop_gap_fill_policy = "stop_price"
    stop = _order("s")
    stop.stop_price = 8.0
    assert _stop_fill_price(stop_price_policy, stop, 9.0, True, 0) == 8.0
    stop_price_policy.config.stop_gap_fill_policy = "open_price"
    assert _stop_fill_price(stop_price_policy, stop, 9.0, False, 1) == 8.0


def test_generated_adapter_runtime_provider_branches_and_bar_index_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PlotRecorder:
        def __init__(self) -> None:
            self.windows: list[tuple[object, object]] = []

        def set_time_window(self, a: object, b: object) -> None:
            self.windows.append((a, b))

    @dataclass(frozen=True)
    class BarState:
        islast: bool = False
        ishistory: bool = True
        isrealtime: bool = False
        isnew: bool = True
        isconfirmed: bool = True

    class Runtime:
        def __init__(self) -> None:
            self.config = types.SimpleNamespace(extra={})
            self.timeframe = types.SimpleNamespace(interval_ms=60_000)
            self.plot_recorder = PlotRecorder()
            self.request_data_end_ms = None
            self.bar_index = 0
            self.barstate = BarState()

        def begin_bar(self, bar: object) -> None:
            self.bar = bar

        def end_bar(self) -> None:
            self.ended = True

    runtime = Runtime()
    monkeypatch.setattr(
        "backtest_engine.adapters.generated_strategy._make_pine_runtime",
        lambda options: runtime,
    )
    monkeypatch.setattr(
        "backtest_engine.adapters.generated_strategy._to_pine_bar",
        lambda bar, fixed_timeframe_ms=None: types.SimpleNamespace(
            time=bar.time, time_close=bar.time_close
        ),
    )

    class Generated:
        def __init__(self, params=None, runtime=None):
            self.ctx = None

        def _process_bar(self, bar):
            return None

    adapter_cls = make_generated_strategy_adapter(
        Generated,
        options=GeneratedStrategyAdapterOptions(
            data_provider="dp", intrabar_provider="ip"
        ),
    )
    adapter_cls.runtime_capture_plots = False
    adapter = adapter_cls(
        ctx=StrategyContext(
            BacktestConfig(symbol="S", timeframe="1", start_time=0, end_time=1),
            state=StrategyStateView(position_avg_price=0.0),
        )
    )
    assert runtime.data_provider == "dp"
    assert runtime.intrabar_provider == "ip"
    assert runtime.plot_recorder.windows == [(1, 0)]
    with pytest.raises(GeneratedStrategyBridgeError, match="bar index mismatch"):
        adapter._process_bar(_bar(0), 2)


def test_strategy_command_processor_nan_and_close_percent_branches() -> None:
    engine = _FakeEngine()
    ctx = StrategyContext(engine.config)
    ctx.entry("N", "long", qty=1, limit=float("nan"))
    flush_strategy_commands(engine, ctx, _bar(), 0)
    assert engine.orders[-1].limit_price is None

    engine = _FakeEngine()
    ctx = StrategyContext(engine.config)
    ctx.close("L", qty_percent=50)
    flush_strategy_commands(engine, ctx, _bar(), 0)
    assert engine.orders[-1].kind == "close"

    flat_engine = types.SimpleNamespace(position=Position(), orders=[])
    assert _pending_full_close_for_current_position(flat_engine) is False


def test_timeframe_and_price_path_remaining_fallback_branches() -> None:
    from backtest_engine.core.price_path import price_path
    from backtest_engine.models.timeframe import _fallback_duration_ms

    assert _fallback_duration_ms("") is None
    engine = types.SimpleNamespace(
        config=types.SimpleNamespace(
            fill_model="intrabar",
            use_bar_magnifier=True,
            bar_magnifier_lower_tf=None,
            bar_magnifier_bars=None,
        )
    )
    path = price_path(engine, _bar(0))
    assert path
