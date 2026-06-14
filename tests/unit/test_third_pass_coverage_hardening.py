from __future__ import annotations

import json
import runpy
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from backtest_engine import BacktestConfig
from backtest_engine.batch.process_pool import run_process_pool
from backtest_engine.batch.runner import BatchBacktestRunner
from backtest_engine.broker.commission import calculate_commission
from backtest_engine.cli.main import main as cli_main
from backtest_engine.core import deterministic_hash
from backtest_engine.core.early_stop import EarlyStopChecker
from backtest_engine.core.engine_validation import validate_backtest_config
from backtest_engine.core.execution_backend_adapter import (
    backend_runtime_warnings,
    ensure_executable_backend,
)
from backtest_engine.core.execution_mode import (
    ExecutionMode,
    is_debug_mode,
    is_fast_mode,
    normalize_execution_mode,
)
from backtest_engine.core.lifecycle import RunLifecycle
from backtest_engine.core.price_path import (
    infer_parent_close,
    limit_fill_price,
    price_path,
    validate_lower_timeframe_bars,
)
from backtest_engine.core.resume_state import restore_resume_state
from backtest_engine.core.validation import data_fingerprint, validate_bars
from backtest_engine.errors import (
    BarValidationError,
    ConfigError,
    ResumeUnsupportedError,
    UnsupportedInstrumentModelError,
)
from backtest_engine.execution_backends.base import BackendExecutionResult
from backtest_engine.models import (
    BacktestJob,
    BacktestResumeState,
    Bar,
    BarSeries,
    Order,
)
from backtest_engine.models.instrument import InstrumentModel
from backtest_engine.models.timeframe import (
    _fallback_duration_ms,
    infer_close_from_timeframe,
)
from backtest_engine.reporting.benchmark_report import render as render_benchmark
from backtest_engine.reporting.compare_report import render as render_compare
from backtest_engine.results import (
    BacktestResult,
    CSVTradeWriter,
    JSONResultWriter,
    compare_trades,
    load_trades_csv,
)
from backtest_engine.results.content_hash import result_content_hash
from backtest_engine.results.comparison import _row
from backtest_engine.results.writers import CSVTradeWriter as DirectCSVTradeWriter


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
        self.seen: list[int] = []

    def _process_bar(self, bar: Bar, index: int) -> None:
        self.seen.append(index)

    def _finalize(self) -> None:
        self.finalized = True


def _bars() -> BarSeries:
    return BarSeries.from_records(
        [
            {
                "time": 1_000,
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 1,
                "time_close": 60_999,
            },
            {
                "time": 61_000,
                "open": 10.5,
                "high": 12,
                "low": 10,
                "close": 11,
                "volume": 2,
                "time_close": 120_999,
            },
        ]
    )


def test_package_main_no_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["backtest"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("backtest_engine", run_name="__main__")
    assert exc.value.code == 0
    assert "Commands:" in capsys.readouterr().out


def test_cli_export_compare_benchmark_and_batch(tmp_path: Path) -> None:
    strategy_path = tmp_path / "strategy.py"
    strategy_path.write_text(
        "class Strat:\n"
        "    def __init__(self, params=None, runtime=None, ctx=None):\n"
        "        self.ctx = ctx\n"
        "    def _process_bar(self, bar, i):\n"
        "        return None\n"
    )
    bars_path = tmp_path / "bars.json"
    bars_path.write_text(
        json.dumps(
            {
                "bars": [
                    {
                        "time": 1000,
                        "open": 1,
                        "high": 2,
                        "low": 0.5,
                        "close": 1.5,
                        "volume": 1,
                        "time_close": 60999,
                    },
                    {
                        "time": 61000,
                        "open": 1.5,
                        "high": 2.5,
                        "low": 1,
                        "close": 2,
                        "volume": 1,
                        "time_close": 120999,
                    },
                ]
            }
        )
    )
    params_path = tmp_path / "params.json"
    params_path.write_text('{"alpha": 1}')

    run_output = tmp_path / "run.json"
    assert (
        cli_main(
            [
                "run",
                "--strategy",
                str(strategy_path),
                "--class",
                "Strat",
                "--bars",
                str(bars_path),
                "--symbol",
                "BTCUSDT",
                "--timeframe",
                "1",
                "--params",
                str(params_path),
                "--output",
                str(run_output),
                "--no-events",
                "--no-equity-curve",
            ]
        )
        == 0
    )
    assert json.loads(run_output.read_text())["status"] == "completed"

    tv_csv = tmp_path / "tv.csv"
    tv_csv.write_text("entry_time,exit_time,entry_price,exit_price,qty,profit\n")
    compare_out = tmp_path / "compare.txt"
    assert (
        cli_main(
            [
                "compare",
                "--our",
                str(run_output),
                "--tv",
                str(tv_csv),
                "--output",
                str(compare_out),
                "--format",
                "text",
            ]
        )
        == 0
    )
    assert "matched:" in compare_out.read_text()

    summary = tmp_path / "summary.md"
    trades_csv = tmp_path / "trades.csv"
    assert (
        cli_main(
            [
                "export",
                "--input",
                str(run_output),
                "--summary-md",
                str(summary),
                "--trades-csv",
                str(trades_csv),
            ]
        )
        == 0
    )
    assert "Backtest summary" in summary.read_text()

    benchmark_out = tmp_path / "benchmark.txt"
    assert (
        cli_main(
            [
                "benchmark",
                "--strategy",
                str(strategy_path),
                "--class",
                "Strat",
                "--bars",
                str(bars_path),
                "--symbol",
                "BTCUSDT",
                "--timeframe",
                "1",
                "--runs",
                "1",
                "--output",
                str(benchmark_out),
                "--format",
                "text",
            ]
        )
        == 0
    )
    assert "Backtest benchmark" in benchmark_out.read_text()

    jobs = tmp_path / "jobs.json"
    jobs.write_text('[{"job_id": "a"}, {"job_id": "b", "params": {"x": 1}}]')
    batch_out = tmp_path / "batch.json"
    assert (
        cli_main(
            [
                "batch",
                "--strategy",
                str(strategy_path),
                "--class",
                "Strat",
                "--bars",
                str(bars_path),
                "--symbol",
                "BTCUSDT",
                "--timeframe",
                "1",
                "--jobs",
                str(jobs),
                "--backend",
                "sequential",
                "--output",
                str(batch_out),
            ]
        )
        == 0
    )
    assert sorted(json.loads(batch_out.read_text())) == ["a", "b"]


def test_process_pool_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenExecutor:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "BrokenExecutor":
            raise ValueError("not picklable")

        def __exit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr(
        "backtest_engine.batch.process_pool.ProcessPoolExecutor", BrokenExecutor
    )
    job = BacktestJob("j", NoopStrategy, bars=_bars())
    with pytest.raises(RuntimeError, match="process batch backend"):
        run_process_pool(
            BacktestConfig(
                symbol="BTCUSDT", timeframe="1", start_time=0, end_time=99_999
            ),
            [job],
        )


def test_validation_early_stop_execution_mode_and_lifecycle() -> None:
    with pytest.raises(ConfigError, match="margin"):
        validate_backtest_config(
            BacktestConfig(
                symbol="BTC", timeframe="1", start_time=0, end_time=1, margin_long=0
            )
        )
    with pytest.raises(ConfigError, match="streaming"):
        validate_backtest_config(
            BacktestConfig(
                symbol="BTC",
                timeframe="1",
                start_time=0,
                end_time=1,
                tradingview_compare_mode="streaming",
            )
        )

    cfg = BacktestConfig(
        symbol="BTC",
        timeframe="1",
        start_time=0,
        end_time=10,
        early_stop_enabled=True,
        min_equity_stop=90,
        max_drawdown_stop_percent=5,
        max_bars_without_trade=2,
    )
    checker = EarlyStopChecker(cfg)
    assert (
        checker.check(
            equity=89, drawdown_percent=0, bar_index=1, last_trade_bar=None
        ).reason
        == "min_equity_stop"
    )
    assert (
        checker.check(
            equity=100, drawdown_percent=6, bar_index=1, last_trade_bar=None
        ).reason
        == "max_drawdown_stop_percent"
    )
    assert (
        checker.check(
            equity=100, drawdown_percent=1, bar_index=5, last_trade_bar=3
        ).reason
        == "max_bars_without_trade"
    )
    assert (
        EarlyStopChecker(
            BacktestConfig(symbol="BTC", timeframe="1", start_time=0, end_time=1)
        )
        .check(equity=0, drawdown_percent=100, bar_index=1, last_trade_bar=0)
        .should_stop
        is False
    )

    assert normalize_execution_mode("debug") is ExecutionMode.DEBUG
    assert is_debug_mode(ExecutionMode.DEBUG)
    assert is_fast_mode("ultra_fast")
    with pytest.raises(ValueError, match="unknown execution mode"):
        normalize_execution_mode("turbo")

    lifecycle = RunLifecycle(started_at=0.0)
    lifecycle.stop_early("x")
    assert lifecycle.status == "early_stopped"
    lifecycle.fail()
    assert lifecycle.status == "failed"
    assert lifecycle.elapsed_ms >= 0


def test_model_hash_reporting_and_writer_edges(tmp_path: Path) -> None:
    @dataclass
    class Obj:
        value: int

    assert json.loads(deterministic_hash.stable_json({"s": {2, 1}, "o": Obj(3)}))[
        "s"
    ] == [1, 2]
    assert isinstance(deterministic_hash.sha256_obj(types.SimpleNamespace(x=1)), str)

    class ResultWithHash:
        def content_hash(
            self, include_equity_curve: bool = True, include_events: bool = False
        ) -> str:
            return f"{include_equity_curve}:{include_events}"

    assert (
        result_content_hash(
            ResultWithHash(), include_equity_curve=False, include_events=True
        )
        == "False:True"
    )
    assert result_content_hash(
        types.SimpleNamespace(to_dict=lambda: {"x": 1})
    ) == deterministic_hash.sha256_obj({"x": 1})
    assert result_content_hash({"x": 1}) == deterministic_hash.sha256_obj({"x": 1})

    assert "runs" in render_benchmark({"runs": 1}, format="text")
    mismatch = compare_trades(
        [
            types.SimpleNamespace(
                entry_time=1, exit_time=2, entry_price=1, exit_price=2, qty=1, profit=1
            )
        ],
        [
            {
                "entry_time": "x",
                "exit_time": 2,
                "entry_price": 1,
                "exit_price": 2,
                "qty": 1,
                "profit": 1,
            }
        ],
    )
    assert not mismatch.matched
    assert "TRADINGVIEW_COMPARE_MISMATCH" in render_compare(mismatch, format="text")
    assert _row(types.SimpleNamespace(z=1, _hidden=2))["z"] == 1

    empty_csv = tmp_path / "empty.csv"
    DirectCSVTradeWriter().write(
        types.SimpleNamespace(closed_trades=[]), str(empty_csv)
    )
    assert empty_csv.read_text() == ""
    json_path = tmp_path / "r.json"
    JSONResultWriter().write({"a": object()}, str(json_path))
    assert "object" in json_path.read_text()


def test_timeframe_bar_series_instrument_and_price_path_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _fallback_duration_ms("2h") == 7_200_000
    assert _fallback_duration_ms("M") is None
    assert _fallback_duration_ms("bad") is None
    assert infer_close_from_timeframe(1000, "1") == 61_000
    with pytest.raises(BarValidationError):
        infer_close_from_timeframe(1000, "1M")

    with pytest.raises(ValueError, match="equal length"):
        BarSeries(time=[1], open=[], high=[1], low=[1], close=[1])
    with pytest.raises(ValueError, match="volume"):
        BarSeries(time=[1], open=[1], high=[1], low=[1], close=[1], volume=[])
    with pytest.raises(ValueError, match="time_close"):
        BarSeries(time=[1], open=[1], high=[1], low=[1], close=[1], time_close=[])

    instrument = InstrumentModel(mode="inverse_futures", contract_size=100.0)
    assert instrument.pnl(100.0, 110.0, 2.0, "long") > 0
    with pytest.raises(UnsupportedInstrumentModelError):
        InstrumentModel(mode="weird").pnl(1, 2, 1, "long")  # type: ignore[arg-type]

    engine = types.SimpleNamespace(
        config=BacktestConfig(
            symbol="BTC",
            timeframe="1",
            start_time=0,
            end_time=1,
            fill_model="close_only",
        )
    )
    assert price_path(engine, Bar(1, 1, 2, 0.5, 1.5)) == [(1.5, "close")]
    order = Order(
        id="L",
        kind="entry",
        side="buy",
        direction="long",
        position_effect="open",
        order_type="limit",
        qty=1,
        created_bar_index=0,
        created_time=1,
        active_from_bar_index=0,
        limit_price=10,
    )
    engine.config.limit_gap_fill_policy = "tradingview"
    assert limit_fill_price(engine, order, 9, True) == 9
    assert infer_parent_close(engine, 1000) == 61_000

    bad_lower = BarSeries.from_bars([Bar(1_000, 1, 2, 0.5, 1.5, time_close=None)])
    with pytest.raises(BarValidationError, match="missing time_close"):
        validate_lower_timeframe_bars(
            engine, bad_lower, Bar(1_000, 1, 2, 0.5, 1.5, time_close=61_000)
        )


def test_validate_bars_policies_and_data_fingerprint() -> None:
    series = BarSeries.from_bars(
        [
            Bar(1, 1, 2, 0.5, 1, 1),
            Bar(1, 2, 3, 1.5, 2, 1),
        ]
    )
    with pytest.raises(BarValidationError, match="Duplicate"):
        validate_bars(series, "error")
    kept_first, _ = validate_bars(series, "keep_first")
    kept_last, _ = validate_bars(series, "keep_last")
    assert kept_first.get_bar(0).open == 1
    assert kept_last.get_bar(0).open == 2
    assert isinstance(data_fingerprint(kept_first), str)

    with pytest.raises(BarValidationError, match="invalid OHLC high"):
        validate_bars(BarSeries.from_bars([Bar(1, 3, 2, 1, 3)]), "error")
    with pytest.raises(BarValidationError, match="invalid OHLC low"):
        validate_bars(BarSeries.from_bars([Bar(1, 1, 2, 3, 1)]), "error")
    with pytest.raises(BarValidationError, match="negative volume"):
        validate_bars(BarSeries.from_bars([Bar(1, 1, 2, 0.5, 1, -1)]), "error")


def test_resume_restore_error_paths_and_batch_unknown_backend() -> None:
    class Engine:
        def __init__(self) -> None:
            self.config = BacktestConfig(
                symbol="BTC", timeframe="1", start_time=0, end_time=1
            )

        def _config_hash(self) -> str:
            return "cfg"

    resume = BacktestResumeState(
        bar_index=0, config_snapshot_hash="cfg", broker_state=None
    )
    with pytest.raises(ResumeUnsupportedError, match="missing broker_state"):
        restore_resume_state(Engine(), resume, object(), object(), object())

    with pytest.raises(ValueError, match="unknown batch backend"):
        BatchBacktestRunner(BacktestConfig(symbol="BTC", timeframe="1", start_time=0, end_time=1), backend="bad").run([])  # type: ignore[arg-type]


def test_commission_and_backend_warning_defaults() -> None:
    assert calculate_commission(100, 2, "fixed_per_order", 1.25) == 1.25
    assert calculate_commission(100, 2, "fixed_per_contract", 0.5) == 1.0
    assert calculate_commission(100, 2, "percent", 1.0) == 2.0
    assert (
        backend_runtime_warnings(BackendExecutionResult(bar_results=[], diagnostics={}))
        == []
    )

    class NotBackend:
        execute = 3

    with pytest.raises(ConfigError):
        ensure_executable_backend(NotBackend())  # type: ignore[arg-type]


def test_distribution_cli_and_protocol_imports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import backtest_engine.protocols as protocols
    from backtest_engine.distribution import main as distribution_main

    assert protocols.ResultWriter is not None
    assert distribution_main(["manifest", "--root", "."]) == 0
    assert "forbidden_count" in capsys.readouterr().out
    out = tmp_path / "pkg.zip"
    assert (
        distribution_main(
            ["build-zip", "--root", ".", "--output", str(out), "--archive-root", "pkg"]
        )
        == 0
    )
    assert out.exists()


def test_contract_bar_conversion_with_fake_marketdata_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contracts_mod = types.ModuleType("marketdata_provider.contracts")
    bar_mod = types.ModuleType("marketdata_provider.contracts.bar")

    @dataclass
    class ContractBar:
        instrument: Any
        timeframe: Any
        time: int
        time_close: int
        open: float
        high: float
        low: float
        close: float
        volume: float | None = None
        closed: bool = True

    bar_mod.Bar = ContractBar
    monkeypatch.setitem(sys.modules, "marketdata_provider.contracts", contracts_mod)
    monkeypatch.setitem(sys.modules, "marketdata_provider.contracts.bar", bar_mod)

    from backtest_engine.models.bar import from_contract_bar, to_contract_bar

    timeframe = types.SimpleNamespace(duration_ms=60_000)
    converted = to_contract_bar(
        Bar(1000, 1, 2, 0.5, 1.5, 10),
        instrument="BTC",
        timeframe=timeframe,
        closed=False,
    )
    assert converted.time_close == 60_999
    assert converted.closed is False
    assert from_contract_bar(converted).time_close == 60_999

    calendar_tf = types.SimpleNamespace(duration_ms=None)
    with pytest.raises(ValueError, match="time_close is required"):
        to_contract_bar(
            Bar(1000, 1, 2, 0.5, 1.5), instrument="BTC", timeframe=calendar_tf
        )


def test_timeframe_with_fake_marketdata_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    contracts_mod = types.ModuleType("marketdata_provider.contracts")

    class InvalidTimeframeError(Exception):
        pass

    def parse_timeframe(value: str) -> Any:
        if value == "bad":
            raise InvalidTimeframeError("bad")
        if value == "1M":
            return types.SimpleNamespace(duration_ms=None)
        return types.SimpleNamespace(duration_ms=123)

    contracts_mod.InvalidTimeframeError = InvalidTimeframeError
    contracts_mod.parse_timeframe = parse_timeframe
    monkeypatch.setitem(sys.modules, "marketdata_provider.contracts", contracts_mod)

    assert infer_close_from_timeframe(10, "x") == 133
    with pytest.raises(BarValidationError):
        infer_close_from_timeframe(10, "bad")
    with pytest.raises(BarValidationError):
        infer_close_from_timeframe(10, "1M")


def test_run_execution_backend_adapter_full_path() -> None:
    from backtest_engine.core.execution_backend_adapter import run_execution_backend
    from backtest_engine.execution_backends.base import (
        BackendBarResult,
        BackendExecutionResult,
    )

    class Backend:
        name = "fake"

        def execute(
            self, strategy_class: type, bars: list[Bar], **kwargs: Any
        ) -> BackendExecutionResult:
            assert strategy_class is NoopStrategy
            assert len(bars) == 2
            assert kwargs["params"] == {"x": 1}
            return BackendExecutionResult(
                bar_results=[BackendBarResult(time=1, phase="score", equity=101.0)],
                diagnostics={"runtime_diagnostics": [{"message": "runtime warn"}]},
                raw_context={"ctx": True},
                raw_result={"result": True},
                plots={"plot": [1]},
            )

    class Engine:
        def __init__(self) -> None:
            self.config = BacktestConfig(
                symbol="BTC", timeframe="1", start_time=0, end_time=100_000
            )
            self.closed_trades: list[Any] = []
            self.open_trades: list[Any] = []
            self._score_equity_points: list[Any] = []
            self._backend_equity_curve: list[Any] = []
            self.warnings: list[Any] = []
            self.equity = self.config.initial_capital
            self.cash = self.config.initial_capital
            self.max_drawdown = 0.0
            self.max_drawdown_percent = 0.0
            self.trough_equity = self.config.initial_capital
            self.max_runup = 0.0
            self.max_runup_percent = 0.0

        def _result(
            self,
            series: BarSeries,
            equity_curve: list[Any],
            status: str,
            early: Any,
            duration: float,
            raw_context: Any,
            raw_result: Any,
        ) -> BacktestResult:
            return BacktestResult(
                status=status,
                equity_curve=equity_curve,
                performance={"raw_context": raw_context, "raw_result": raw_result},
            )

    result = run_execution_backend(
        Engine(), Backend(), NoopStrategy, {"x": 1}, _bars(), 0.0, 0
    )
    assert result.plots == {"plot": [1]}
    assert "plots" in result.available_outputs
    assert result.performance["execution_backend"] == "fake"


def test_remaining_small_edges(tmp_path: Path) -> None:
    assert calculate_commission(1, 1, "none", 99) == 0
    with pytest.raises(ValueError, match="unknown commission_type"):
        calculate_commission(1, 1, "weird", 1)
    assert (
        EarlyStopChecker(
            BacktestConfig(
                symbol="BTC",
                timeframe="1",
                start_time=0,
                end_time=1,
                early_stop_enabled=True,
            )
        )
        .check(equity=100, drawdown_percent=0, bar_index=0, last_trade_bar=None)
        .reason
        is None
    )
    assert deterministic_hash.stable_json(object()).startswith('"<object object')
    assert render_compare(compare_trades([], []), format="json").strip().startswith("{")

    class Result:
        closed_trades = [{"entry_id": "L", "profit": 1}]

    csv_path = tmp_path / "trades.csv"
    CSVTradeWriter().write(Result(), str(csv_path))
    assert "entry_id" in csv_path.read_text()
    assert load_trades_csv(csv_path)[0]["entry_id"] == "L"
