from __future__ import annotations

import runpy
import sys
import tomllib
import types
from pathlib import Path
from typing import Any, cast

import pytest

from backtest_engine import BacktestConfig, BacktestEngine
from backtest_engine.context import StrategyContext
from backtest_engine.core.engine_realtime import RealtimeExecutionCheckpoint
from backtest_engine.core.fill_scanner import _fill_price_for_order, process_bar_fills
from backtest_engine.core.margin_call import maybe_margin_call
from backtest_engine.core.native_run_loop import _early_stop_state, run_native_strategy
from backtest_engine.core.price_path import (
    limit_fill_price,
    price_path,
    validate_lower_timeframe_bars,
)
from backtest_engine.core.realtime import build_bar_tick_schedule
from backtest_engine.core.resume_state import restore_resume_state
from backtest_engine.core.state_snapshot import (
    RealtimeBrokerSnapshot,
    _plain,
)
from backtest_engine.errors import (
    BarMagnifierUnavailableError,
    BarValidationError,
    ConfigError,
    ResumeUnsupportedError,
)
from backtest_engine.models import (
    BacktestCallbacks,
    BacktestResumeState,
    Bar,
    BarSeries,
    Order,
    Position,
    Tick,
    Trade,
)
from backtest_engine.reporting.monte_carlo_report import render as render_monte_carlo
from backtest_engine.results.comparison import compare_trades, load_trades_csv
from backtest_engine.results.equity_curve import EquityPoint, final_equity


_MISSING_MODULE = object()


def _run_module_as_main(module_name: str) -> dict[str, Any]:
    imported = sys.modules.pop(module_name, _MISSING_MODULE)
    try:
        return runpy.run_module(module_name, run_name="__main__")
    finally:
        if imported is _MISSING_MODULE:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = cast(types.ModuleType, imported)


def test_run_module_as_main_restores_missing_module_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "backtest_engine._runpy_restore_probe"

    def fake_run_module(name: str, *, run_name: str) -> dict[str, Any]:
        assert name == module_name
        assert run_name == "__main__"
        sys.modules[module_name] = types.ModuleType(module_name)
        raise SystemExit(0)

    monkeypatch.setattr(runpy, "run_module", fake_run_module)
    sys.modules.pop(module_name, None)
    with pytest.raises(SystemExit):
        _run_module_as_main(module_name)
    assert module_name not in sys.modules


def _bar(
    time: int = 0,
    *,
    open_: float = 10.0,
    high: float = 12.0,
    low: float = 8.0,
    close: float = 11.0,
) -> Bar:
    return Bar(
        time=time,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        time_close=time + 59_999,
    )


def _series() -> BarSeries:
    return BarSeries.from_bars([_bar(0), _bar(60_000, open_=11.0, close=12.0)])


def _order(
    order_id: str = "L",
    *,
    kind: str = "entry",
    status: str = "active",
    direction: str = "long",
    side: str = "buy",
    effect: str = "open",
    order_type: str = "market",
    qty: float = 1.0,
    created: int = 0,
    from_entry: str | None = None,
) -> Order:
    return Order(
        id=order_id,
        kind=kind,  # type: ignore[arg-type]
        direction=direction,  # type: ignore[arg-type]
        side=side,  # type: ignore[arg-type]
        position_effect=effect,  # type: ignore[arg-type]
        order_type=order_type,  # type: ignore[arg-type]
        qty=qty,
        created_bar_index=created,
        created_time=0,
        active_from_bar_index=created,
        position_direction=direction if direction in {"long", "short"} else None,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        from_entry=from_entry,
    )


def _trade(
    entry_id: str = "L",
    *,
    qty: float = 1.0,
    direction: str = "long",
    profit: float = 1.0,
) -> Trade:
    return Trade(
        id=f"t-{entry_id}",
        entry_id=entry_id,
        exit_id=None,
        direction=direction,  # type: ignore[arg-type]
        entry_time=0,
        entry_bar_index=0,
        entry_price=10.0,
        exit_time=None,
        exit_bar_index=None,
        exit_price=None,
        qty=qty,
        commission_entry=0.0,
        commission_exit=0.0,
        profit=profit,
        profit_percent=0.0,
        is_open=True,
    )


class NoopStrategy:
    def __init__(
        self,
        params: dict[str, Any] | None = None,
        runtime: Any | None = None,
        ctx: Any | None = None,
    ) -> None:
        self.params = params or {}
        self.runtime = runtime
        self.ctx = ctx
        self.finalized = False

    def _process_bar(self, bar: Bar, i: int) -> None:
        return None

    def _finalize(self) -> None:
        self.finalized = True


def test_engine_public_wrappers_and_fail_closed_paths() -> None:
    cfg = BacktestConfig(symbol="BTC", timeframe="1", start_time=0, end_time=120_000, finality_policy="ALLOW_OPEN")
    engine = BacktestEngine(cfg)
    result = engine.process_next_bar(NoopStrategy, _bar(0))
    assert result.status == "completed"

    with pytest.raises(BarMagnifierUnavailableError):
        BacktestEngine(
            BacktestConfig(
                symbol="BTC",
                timeframe="1",
                start_time=0,
                end_time=1,
                use_bar_magnifier=True,
            finality_policy="ALLOW_OPEN",
             )
        ).run(NoopStrategy, bars=[_bar(0)])

    class Backend:
        name = "minimal"

        def execute(self, strategy_class: type, bars: list[Bar], **kwargs: Any) -> Any:
            from backtest_engine.execution_backends.base import BackendExecutionResult

            return BackendExecutionResult(bar_results=[], diagnostics={})

    backend_result = BacktestEngine(cfg).run(
        NoopStrategy, bars=[_bar(0)], execution_backend=Backend()
    )
    assert backend_result.performance["execution_backend"] == "minimal"


def test_engine_private_wrappers_reservations_and_callbacks() -> None:
    engine = BacktestEngine(
        BacktestConfig(
            symbol="BTC", timeframe="1", start_time=0, end_time=1, pyramiding=0
        ,
         finality_policy="ALLOW_OPEN"
         )
    )
    engine.position = Position(size=1.0, avg_price=10.0, direction="long")
    engine.orders = [_order("pending", status="pending")]
    assert engine._entry_allowed("long") is False
    assert isinstance(engine._pending_entry_position_delta(), float)

    engine.open_trades = [_trade("A", qty=0.0), _trade("B", qty=2.0)]
    missing_entry_exit = _order(
        "X", kind="exit", effect="reduce", qty=1.0, from_entry="NOPE"
    )
    missing_entry_exit.parent_exit_id = "group1"
    broad_exit = _order("Y", kind="exit", effect="reduce", qty=1.0, from_entry=None)
    broad_exit.parent_exit_id = "group2"
    engine.orders = [missing_entry_exit, broad_exit]
    reserved = engine._reserved_qty_by_entry()
    assert reserved == {"B": 1.0}

    trailing = _order("trail", kind="exit", effect="reduce", qty=1.0, from_entry="B")
    trailing.trail_offset = 1.0
    engine._update_trailing_order(trailing, 13.0)
    assert trailing.trail_activated is True

    engine.config.collect_mfe_mae = False
    engine._update_trade_excursions(_bar(0))

    engine._closed_trade_stats_count = 5
    engine.closed_trades = []
    engine._gross_profit_total = engine._gross_loss_total = 10.0
    engine._win_trades_total = engine._loss_trades_total = engine._even_trades_total = 1
    engine._update_state()
    assert engine._closed_trade_stats_count == 0

    callbacks = BacktestCallbacks(
        on_diagnostic=lambda diag: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    engine.callbacks = callbacks
    with pytest.raises(RuntimeError):
        engine._diag("X", "boom", "warning")


def test_bar_magnifier_price_path_and_lower_tf_edges() -> None:
    parent = _bar(0)
    cfg = BacktestConfig(
        symbol="BTC",
        timeframe="1",
        start_time=0,
        end_time=60_000,
        use_bar_magnifier=True,
        bar_magnifier_lower_tf="1S",
        bar_magnifier_bars={0: [Bar(0, 1, 2, 0.5, 1.5, time_close=60_001)]},
    finality_policy="ALLOW_OPEN",
     )
    engine = BacktestEngine(cfg)
    with pytest.raises(BarMagnifierUnavailableError):
        price_path(engine, parent)

    empty_engine = BacktestEngine(
        BacktestConfig(
            symbol="BTC",
            timeframe="1",
            start_time=0,
            end_time=60_000,
            use_bar_magnifier=True,
            bar_magnifier_lower_tf="1S",
            bar_magnifier_bars={0: []},
        finality_policy="ALLOW_OPEN",
         )
    )
    with pytest.raises(BarMagnifierUnavailableError, match="empty"):
        price_path(empty_engine, parent)

    lower = BarSeries.from_bars([Bar(0, 1, 2, 0.5, 1.5, time_close=60_001)])
    with pytest.raises(BarValidationError, match="closes outside"):
        validate_lower_timeframe_bars(
            BacktestEngine(BacktestConfig("BTC", "1", 0, 1, finality_policy="ALLOW_OPEN")), lower, parent
        )

    sell_limit = _order("S", side="sell", direction="short", order_type="limit")
    sell_limit.limit_price = 10.0
    eng = types.SimpleNamespace(
        config=BacktestConfig("BTC", "1", 0, 1, limit_gap_fill_policy="tradingview", finality_policy="ALLOW_OPEN")
    )
    assert limit_fill_price(eng, sell_limit, 11.0, True) == 11.0


def test_fill_scanner_trailing_pending_and_margin_recalc_branches() -> None:
    engine = BacktestEngine(
        BacktestConfig(
            "BTC",
            "1",
            0,
            60_000,
            calc_on_order_fills=True,
            margin_long=50.0,
            qty_step=1000.0,
        finality_policy="ALLOW_OPEN",
         )
    )
    trailing = _order(
        "T",
        status="pending",
        kind="exit",
        effect="reduce",
        order_type="stop",
        from_entry="L",
    )
    trailing.trail_price = 10.0
    trailing.trail_offset = 1.0
    engine.orders = [trailing]
    process_bar_fills(
        engine,
        NoopStrategy({}, None, StrategyContext(engine.config)),
        StrategyContext(engine.config),
        _bar(0),
        0,
    )
    assert trailing.status == "pending"

    # q <= 0 after floor rounding should fail closed without a liquidation fill.
    engine.position = Position(
        size=1.0, avg_price=100.0, direction="long", open_profit=-100.0
    )
    engine.cash = 0.0
    engine.equity = 0.0
    assert maybe_margin_call(engine, 1.0, _bar(0), 0, "low") is False

    open_order = _order("O", kind="entry", order_type="market", created=0)
    engine2 = types.SimpleNamespace(
        config=BacktestConfig("BTC", "1", 0, 1, calc_on_order_fills=True, finality_policy="ALLOW_OPEN"),
        _matching_open_trades=lambda from_entry: [],
        _limit_fill_price=lambda order, price, open_: price,
    )
    assert (
        _fill_price_for_order(engine2, open_order, _bar(0), 0, 11.0, "open", True)
        == 11.0
    )


def test_native_run_loop_legacy_strategy_signature_and_early_stops() -> None:
    class LegacyStrategy:
        def __init__(self, params: dict[str, Any], runtime: Any) -> None:
            self.params = params
            self.runtime = runtime

        def _process_bar(self, bar: Bar, i: int) -> None:
            return None

    engine = BacktestEngine(
        BacktestConfig(
            "BTC", "1", 0, 120_000, required_outputs=set(), collect_equity_curve=False
        ,
         finality_policy="ALLOW_OPEN"
         )
    )
    result = run_native_strategy(engine, LegacyStrategy, {}, _series(), 0.0, None)
    assert result.status == "completed"

    engine._early_stop_enabled = True
    engine._max_drawdown_stop_percent = 10.0
    extremes = types.SimpleNamespace(drawdown=0.0, drawdown_percent=11.0)
    assert _early_stop_state(engine, 0, extremes)[2] == "max_drawdown_stop_percent"
    engine._max_drawdown_stop_percent = None
    engine._max_drawdown_stop_cash = 5.0
    extremes.drawdown = 6.0
    assert _early_stop_state(engine, 0, extremes)[2] == "max_drawdown_stop_cash"
    engine._max_drawdown_stop_cash = None
    engine._max_bars_without_trade = 2
    engine.last_trade_bar = 0
    assert _early_stop_state(engine, 2, extremes)[2] == "max_bars_without_trade"


def test_realtime_checkpoint_runtime_fallback_and_restore_errors() -> None:
    engine = BacktestEngine(BacktestConfig("BTC", "1", 0, 1, finality_policy="ALLOW_OPEN"))

    class Runtime:
        def export_state(self, include_varip: bool) -> dict[str, bool]:
            raise TypeError("old signature")

    class OldRuntime:
        def export_state(self) -> dict[str, str]:
            return {"mode": "old"}

    checkpoint = engine._export_realtime_execution_checkpoint(runtime=OldRuntime())
    assert checkpoint.runtime_state == {"mode": "old"}

    snapshot = engine._export_realtime_broker_state()
    with pytest.raises(ResumeUnsupportedError, match="strategy_state"):
        engine._restore_realtime_execution_checkpoint(
            RealtimeExecutionCheckpoint(snapshot, strategy_state={"x": 1}),
            strategy=object(),
        )

    with pytest.raises(ResumeUnsupportedError, match="RealtimeExecutionCheckpoint"):
        engine._restore_realtime_execution_checkpoint(object())  # type: ignore[arg-type]


def test_resume_restore_diagnostic_policy_and_runtime_strategy_errors() -> None:
    engine = BacktestEngine(
        BacktestConfig("BTC", "1", 0, 1, resume_validation_policy="diagnostic", finality_policy="ALLOW_OPEN")
    )
    broker = engine._export_realtime_broker_state()
    resume = BacktestResumeState(
        bar_index=1, config_snapshot_hash="mismatch", broker_state=broker
    )
    ctx = StrategyContext(engine.config)
    assert restore_resume_state(engine, resume, object(), object(), ctx) == 2
    assert engine.warnings and engine.warnings[-1].code == "RESUME_CONFIG_MISMATCH"

    bad_runtime = BacktestResumeState(
        bar_index=0,
        config_snapshot_hash=engine._config_hash(),
        broker_state=broker,
        runtime_state={},
    )
    with pytest.raises(ResumeUnsupportedError, match="runtime_state"):
        restore_resume_state(engine, bad_runtime, object(), object(), ctx)
    bad_strategy = BacktestResumeState(
        bar_index=0,
        config_snapshot_hash=engine._config_hash(),
        broker_state=broker,
        strategy_state={},
    )
    with pytest.raises(ResumeUnsupportedError, match="strategy_state"):
        restore_resume_state(engine, bad_strategy, object(), object(), ctx)


def test_result_builder_plot_recorders_and_metric_errors() -> None:
    cfg = BacktestConfig(
        "BTC",
        "1",
        0,
        1,
        required_metrics={"unknown", "sharpe", "sortino"},
        collect_equity_curve=True,
    finality_policy="ALLOW_OPEN",
     )
    engine = BacktestEngine(cfg)
    runtime = types.SimpleNamespace(
        plot_recorder=types.SimpleNamespace(get_records=lambda: [{"name": "p"}])
    )
    result = engine._result(
        _series(), [], "completed", None, 1.0, strategy=None, runtime=runtime
    )
    assert result.plots == [{"name": "p"}]
    assert {d.code for d in engine.errors} >= {
        "REQUIRED_METRIC_UNSUPPORTED",
        "REQUIRED_METRIC_UNAVAILABLE",
    }

    strategy = types.SimpleNamespace(
        _pine_runtime=types.SimpleNamespace(plot_recorder=[{"raw": True}])
    )
    result2 = BacktestEngine(BacktestConfig("BTC", "1", 0, 1, finality_policy="ALLOW_OPEN"))._result(
        _series(), None, "completed", None, 1.0, strategy=strategy
    )
    assert result2.plots == [{"raw": True}]


def test_cli_main_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["backtest-engine"])
    with pytest.raises(SystemExit) as exc:
        _run_module_as_main("backtest_engine.cli.main")
    assert exc.value.code == 0


def test_infra_error_and_main_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backtest_engine import distribution, quality, release

    suspicious = tmp_path / "a__pycache__b.txt"
    suspicious.write_text("x")
    assert distribution.distribution_manifest(tmp_path).forbidden_count == 1
    out = tmp_path / "pkg.zip"
    assert (
        distribution.main(["build-zip", "--root", str(tmp_path), "--output", str(out)])
        == 0
    )
    monkeypatch.setattr(
        sys, "argv", ["distribution", "manifest", "--root", str(tmp_path)]
    )
    with pytest.raises(SystemExit) as exc:
        _run_module_as_main("backtest_engine.distribution")
    assert exc.value.code == 1

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "bad.py").write_text("def nope(:\n")
    (bad / "big.py").write_text("\n".join("x = 1" for _ in range(3)))
    assert quality.duplicate_report(bad).duplicate_group_count == 0
    assert quality.main(["architecture", str(bad), "--max-lines", "1"]) == 1
    monkeypatch.setattr(
        sys, "argv", ["quality", "architecture", str(bad), "--max-lines", "1"]
    )
    with pytest.raises(SystemExit) as exc:
        _run_module_as_main("backtest_engine.quality")
    assert exc.value.code == 1

    relroot = tmp_path / "rel"
    relroot.mkdir()
    (relroot / "pyproject.toml").write_text(
        '[project]\nname = "backtest-engine"\nversion = "0.0.0"\n'
    )
    report = release.release_report(relroot)
    assert not report.ok and report.notes
    assert (
        release.main(["--root", str(relroot), "--json", str(tmp_path / "r.json")]) == 1
    )
    monkeypatch.setattr(sys, "argv", ["release", "--root", str(relroot)])
    with pytest.raises(SystemExit) as exc:
        _run_module_as_main("backtest_engine.release")
    assert exc.value.code == 1


def test_results_reporting_small_edges(tmp_path: Path) -> None:
    assert (
        render_monte_carlo([types.SimpleNamespace(a=1)], format="text")
        .strip()
        .startswith("{'a': 1")
    )
    csv_path = tmp_path / "trades.csv"
    csv_path.write_text(
        "entry_time,exit_time,entry_price,exit_price,qty,profit\n1,2,3,4,5,6\n"
    )
    assert load_trades_csv(csv_path)[0]["profit"] == "6"
    report = compare_trades([1], [1])
    assert report.matched
    assert (
        final_equity(
            [EquityPoint(0, 0, 101.0, 101.0, 0.0, None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)]
        )
        == 101.0
    )


def test_tick_schedule_and_plain_dataclass_edges() -> None:
    bars = BarSeries.from_bars(
        [
            Bar(0, 1, 2, 0.5, 1.5, time_close=None),
            Bar(60_000, 1, 2, 0.5, 1.5, time_close=None),
        ]
    )
    slices = build_bar_tick_schedule(bars, [Tick(10, 1.0), Tick(70_000, 2.0)])
    assert [len(s.ticks) for s in slices] == [1, 1]
    with pytest.raises(ConfigError, match="outside"):
        build_bar_tick_schedule(
            [Bar(10, 1, 2, 0.5, 1.5, time_close=20)], [Tick(25, 1.0)]
        )
    assert (
        _plain(
            RealtimeBrokerSnapshot(
                cash=1,
                equity=1,
                peak_equity=1,
                trough_equity=1,
                max_drawdown=0,
                max_drawdown_percent=0,
                max_runup=0,
                max_runup_percent=0,
                position=Position(),
                orders=[],
                fills=[],
                closed_trades=[],
                open_trades=[],
                last_trade_bar=None,
            )
        )["cash"]
        == 1
    )


def test_second_gap_engine_and_helper_edges() -> None:
    from backtest_engine.context.command_buffer import EntryOrderPayload
    from backtest_engine.core.strategy_command_processor import (
        _apply_entry_or_order_command,
    )
    from backtest_engine.core.fill_scanner import _scan_orders_at_path_point
    from backtest_engine.core.position_accounting import (
        _gross_close_profit,
        _close_target_trades,
        apply_position,
    )

    from backtest_engine.core.realtime import (
        RealtimeOrderFillOracleStatus,
        validate_realtime_order_fill_oracle_proof,
    )
    from backtest_engine.core.validation import validate_bars

    # _entry_allowed existing-order branch.
    engine = BacktestEngine(BacktestConfig("BTC", "1", 0, 1, pyramiding=0, finality_policy="ALLOW_OPEN"))
    engine.orders = [_order("P", status="pending", direction="long")]
    assert engine._entry_allowed("long") is False
    engine._validate_lower_timeframe_bars(
        BarSeries.from_bars([Bar(0, 1, 2, 0.5, 1.5, time_close=1)]),
        Bar(0, 1, 2, 0.5, 1.5, time_close=10),
    )
    assert engine._infer_parent_close(0) == 60_000

    # runtime checkpoint fallback when inspect.signature fails.
    def export_state() -> dict[str, str]:
        return {"fallback": "ok"}

    export_state.__signature__ = object()  # type: ignore[attr-defined]
    checkpoint = engine._export_realtime_execution_checkpoint(
        runtime=types.SimpleNamespace(export_state=export_state)
    )
    assert checkpoint.runtime_state == {"fallback": "ok"}

    # process_bar_fills line 83: filled with calc_on_order_fills but no recalc on close activation.
    close_engine = BacktestEngine(
        BacktestConfig(
            "BTC", "1", 0, 1, calc_on_order_fills=True, process_orders_on_close=True
        ,
         finality_policy="ALLOW_OPEN"
         )
    )
    close_order = _order("C", created=0, order_type="market")
    close_engine.orders = [close_order]
    fills: list[tuple[str, float]] = []
    close_engine._fill = lambda order, bar, i, price, point: fills.append((point, price))  # type: ignore[method-assign]
    close_engine._maybe_margin_call = lambda *args: False  # type: ignore[method-assign]
    process_bar_fills(
        close_engine,
        NoopStrategy(),
        StrategyContext(close_engine.config),
        _bar(0),
        0,
        skip_open=True,
    )
    assert fills

    # close_activation_only same-bar non-close skip branch.
    skip_order = _order("S", created=0)
    skip_order.immediately = True
    skip_engine = BacktestEngine(
        BacktestConfig("BTC", "1", 0, 1, process_orders_on_close=False, finality_policy="ALLOW_OPEN")
    )
    skip_engine.orders = [skip_order]
    assert _scan_orders_at_path_point(
        skip_engine,
        NoopStrategy(),
        StrategyContext(skip_engine.config),
        _bar(0),
        0,
        10.0,
        "open",
        True,
        0,
        0,
        False,
        True,
        False,
        False,
        None,
    ) == (False, 0, False)

    # Trailing activation at open keeps the offset-derived stop; it must not
    # overwrite the stop with the open price and force an immediate open fill.
    trailing = _order(
        "T", kind="exit", effect="reduce", order_type="stop", from_entry=None
    )
    trailing.trail_price = 10.0
    trailing.trail_offset = 1.0
    assert (
        _fill_price_for_order(skip_engine, trailing, _bar(0), 0, 11.0, "open", True)
        == 11.0
    )
    assert trailing.stop_price == 10.0

    # Position-accounting no-target and no-unreserved-qty branches.
    engine2 = BacktestEngine(BacktestConfig("BTC", "1", 0, 1, finality_policy="ALLOW_OPEN"))
    engine2.position = Position(size=1.0, avg_price=10.0, direction="long")
    engine2.open_trades = []
    assert (
        apply_position(
            engine2,
            _order("R", side="sell", kind="close", effect="close", from_entry="NOPE"),
            9.0,
            _bar(0),
            0,
            0.0,
        )
        == "long"
    )
    assert engine2.warnings[-1].code == "ORDER_REJECTED_NO_MATCHING_ENTRY"
    engine2.open_trades = [_trade("A", qty=1.0)]
    reserve_order = _order("RES", kind="exit", effect="reduce", qty=1.0, from_entry="A")
    reserve_order.parent_exit_id = "g"
    engine2.orders = [reserve_order]
    assert (
        apply_position(
            engine2,
            _order("R2", side="sell", kind="exit", effect="reduce", from_entry=None),
            9.0,
            _bar(0),
            0,
            0.0,
        )
        == "long"
    )
    assert engine2.warnings[-1].code == "ORDER_REJECTED_NO_AVAILABLE_POSITION_QTY"

    gross_targets = [_trade("Z", qty=1.0), _trade("Y", qty=1.0)]
    assert (
        _gross_close_profit(
            engine2,
            gross_targets,
            {id(gross_targets[0]): 1.0, id(gross_targets[1]): 1.0},
            0.0,
            9.0,
        )
        == 0.0
    )
    # Directly close no quantity to cover early break in _close_target_trades without mutating state.
    targets = [_trade("Q", qty=1.0)]
    _close_target_trades(
        engine2,
        _order("X", side="sell", kind="exit", effect="reduce"),
        targets,
        {id(targets[0]): 1.0},
        0.0,
        0.0,
        9.0,
        _bar(0),
        0,
    )

    # lower-TF invalid low and supplied-bar no-lower continue branch.
    with pytest.raises(BarValidationError, match="invalid OHLC low"):
        validate_lower_timeframe_bars(
            skip_engine,
            BarSeries.from_bars([Bar(0, 1, 4, 3, 1, time_close=1)]),
            Bar(0, 1, 2, 0.5, 1, time_close=10),
        )
    skip_engine.config.bar_magnifier_bars = {}
    skip_engine._validate_supplied_bar_magnifier_bars(_series())

    # realtime proof incomplete branch.
    with pytest.raises(ConfigError, match="incomplete"):
        validate_realtime_order_fill_oracle_proof(
            RealtimeOrderFillOracleStatus(status="proven").as_proof()
        )
    with pytest.raises(ConfigError, match="time_close"):
        build_bar_tick_schedule([Bar(10, 1, 2, 0.5, 1, time_close=5)], [])

    # Score-mode with no score metrics returns without applying them.
    score_engine = BacktestEngine(BacktestConfig("BTC", "1", 0, 1, finality_policy="ALLOW_OPEN"))
    score_engine._score_mode = True
    score_result = score_engine._result(_series(), [], "completed", None, 1.0)
    assert score_result.score_total_trades == 0

    # Strict resume config mismatch branch.
    strict_engine = BacktestEngine(BacktestConfig("BTC", "1", 0, 1, finality_policy="ALLOW_OPEN"))
    strict_resume = BacktestResumeState(
        0, "bad", broker_state=strict_engine._export_realtime_broker_state()
    )
    with pytest.raises(ResumeUnsupportedError, match="config hash"):
        restore_resume_state(
            strict_engine,
            strict_resume,
            object(),
            object(),
            StrategyContext(strict_engine.config),
        )

    # Existing order + risk rejection branch in entry/order command processor.
    proc_engine = BacktestEngine(BacktestConfig("BTC", "1", 0, 1, pyramiding=10, finality_policy="ALLOW_OPEN"))
    proc_engine.orders = [_order("E", status="active")]
    proc_engine._risk_allows_order = lambda *args: False  # type: ignore[method-assign]
    _apply_entry_or_order_command(
        proc_engine,
        "entry",
        EntryOrderPayload("E", "long", qty=1.0),
        _bar(0),
        0,
        False,
        None,
        None,
        "market",
    )
    assert proc_engine.orders[0].qty == 1.0
    pending_close_engine = BacktestEngine(
        BacktestConfig("BTC", "1", 0, 1, pyramiding=10, finality_policy="ALLOW_OPEN")
    )
    pending_close_engine.position = Position(size=1.0, avg_price=10.0, direction="long")
    pending_close_engine.orders = [
        _order(
            "closeL",
            kind="close",
            effect="close",
            qty=1.0,
            direction="long",
            side="sell",
            status="active",
        )
    ]
    _apply_entry_or_order_command(
        pending_close_engine,
        "entry",
        EntryOrderPayload("S", "short", qty=1.0),
        _bar(0),
        0,
        False,
        None,
        None,
        "market",
    )
    assert pending_close_engine.orders[-1].position_effect == "open"


    # validate_bars sorted branch and comparison missing-field branch.
    with pytest.raises(BarValidationError, match="not sorted"):
        validate_bars(BarSeries.from_bars([_bar(10), _bar(0)]))
    assert compare_trades([{"entry_time": 1}], [{"entry_time": 1}]).matched


def test_last_tiny_gap_branches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import dataclass
    from backtest_engine import distribution, quality
    from backtest_engine.core.result_builder import _apply_score_window_metrics
    from backtest_engine.results import BacktestResult

    assert distribution._should_include(Path("x.pyc")) is False
    assert quality.main(["duplicates", "backtest_engine"]) == 0
    assert "duplicate_group_count" in capsys.readouterr().out

    empty_score_engine = types.SimpleNamespace(
        closed_trades=[], _score_equity_points=[], _score_start_index=0
    )
    result = BacktestResult()
    _apply_score_window_metrics(empty_score_engine, result)
    assert result.score_total_trades == 0

    @dataclass
    class Row:
        entry_time: int = 1
        exit_time: int = 2
        entry_price: float = 1.0
        exit_price: float = 2.0
        qty: float = 1.0
        profit: float = 1.0

    dataclass_report = compare_trades([Row()], [Row()])
    assert dataclass_report.matched
    mismatch_report = compare_trades([Row()], [])
    assert not mismatch_report.matched and mismatch_report.first_mismatch_index == 0


def test_phase0_release_gate_remaining_branches() -> None:
    from backtest_engine.context.command_buffer import EntryOrderPayload
    from backtest_engine.core.fill_execution import (
        _consume_opposite_reverse_close_component,
        _is_limit_gap_open_fill,
    )
    from backtest_engine.core.fill_scanner import _scan_orders_at_path_point
    from backtest_engine.core.position_accounting import _closed_trade_exit_prices
    from backtest_engine.core.strategy_command_processor import _apply_entry_or_order_command
    from backtest_engine.models.timeframe import infer_close_from_timeframe

    assert infer_close_from_timeframe(1_000, "1") == 61_000
    with pytest.raises(BarValidationError, match="duration is unknown"):
        infer_close_from_timeframe(1_000, "1M")

    engine = BacktestEngine(BacktestConfig("BTC", "1", 0, 1, pyramiding=10, finality_policy="ALLOW_OPEN"))
    bar = _bar(0)
    filled = _order("filled", kind="close", effect="close", side="sell", qty=1.0)
    _consume_opposite_reverse_close_component(engine, filled, "flat", bar, 0)
    filled.qty = 0.0
    _consume_opposite_reverse_close_component(engine, filled, "long", bar, 0)

    filled.qty = 1.0
    wrong_direction = _order("wrong", effect="reverse", direction="long", qty=2.0)
    reduced_reverse = _order("reverse", effect="reverse", direction="short", qty=2.0)
    engine.orders = [filled, wrong_direction, reduced_reverse]
    _consume_opposite_reverse_close_component(engine, filled, "long", bar, 0)
    assert wrong_direction.qty == 2.0
    assert reduced_reverse.qty == 1.0
    assert reduced_reverse.position_effect == "open"

    no_limit = _order("no-limit", kind="exit", effect="reduce", side="buy")
    assert _is_limit_gap_open_fill(no_limit, 9.0, "open", 0.01) is False
    no_limit.limit_price = 10.0
    assert _is_limit_gap_open_fill(no_limit, 9.0, "open", 0.01) is True

    trailing = _order("trailing", kind="exit", effect="reduce", order_type="stop")
    trailing.trail_offset = 1.0
    engine.orders = [trailing]
    assert _scan_orders_at_path_point(
        engine,
        NoopStrategy(),
        StrategyContext(engine.config),
        bar,
        0,
        10.0,
        "open",
        True,
        0,
        0,
        False,
        False,
        True,
        False,
        None,
    ) == (False, 0, False)

    ordinary = _order("ordinary")
    engine.orders = [ordinary]
    assert _scan_orders_at_path_point(
        engine,
        NoopStrategy(),
        StrategyContext(engine.config),
        bar,
        0,
        10.0,
        "open",
        True,
        0,
        0,
        False,
        False,
        False,
        True,
        None,
    ) == (False, 0, False)

    bracket = _order("bracket", kind="exit", effect="reduce", from_entry="A")
    bracket.parent_exit_id = "group"
    sibling = _order("sibling", kind="exit", effect="reduce", from_entry="B")
    sibling.parent_exit_id = "group"
    sibling.stop_price = 9.0
    sibling.limit_price = 12.0
    engine.orders = [bracket, sibling]
    assert _closed_trade_exit_prices(engine, bracket) == (None, None)

    active_market = _order("E", status="active", created=0)
    engine.orders = [active_market]
    _apply_entry_or_order_command(
        engine,
        "entry",
        EntryOrderPayload("E", "long", qty=0.0),
        bar,
        0,
        False,
        None,
        None,
        "market",
    )
    assert active_market.status == "active"
    assert active_market.qty == 1.0


def test_release_suite_has_no_skip_xfail_or_importorskip_and_declares_siblings() -> None:
    root = Path(__file__).resolve().parents[2]
    forbidden = (
        "pytest." + "skip",
        "pytest.mark." + "skip",
        "pytest.mark." + "xfail",
        "pytest." + "importorskip",
    )
    offenders: list[str] = []
    for path in sorted((root / "tests").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []

    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = config["project"]["dependencies"]
    assert "pinelib==5.0.0rc6" in dependencies
    assert "marketdata-provider==5.0.0rc6" in dependencies
    assert "openpine-contracts==5.0.0rc6" in dependencies
    assert not any("git+" in str(item) for item in dependencies)
