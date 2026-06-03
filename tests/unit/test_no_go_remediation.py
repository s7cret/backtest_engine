import json
from pathlib import Path

from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.batch import BatchBacktestRunner, BacktestJob
from backtest_engine.cli.main import main as cli_main


def cfg(**kw):
    d = dict(symbol="S", timeframe="1D", start_time=1, end_time=10, commission_type="none")
    d.update(kw)
    return BacktestConfig(**d)


class BuyThenTrailing:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 2:
            self.ctx.exit("XT", from_entry="L", qty=1, trail_points=2, trail_offset=1)


def test_trailing_stop_creates_executable_exit():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 10, 10, 10),
        Bar(3, 10, 10, 10, 10),
        Bar(4, 11, 13, 11, 12),
        Bar(5, 12, 12, 10, 10),
    ]
    r = BacktestEngine(cfg()).run(BuyThenTrailing, bars=bars)
    assert r.closed_trades
    assert r.closed_trades[0].exit_id == "XT:T"
    assert r.closed_trades[0].exit_price == 12


class BuyThenProfitLoss:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 2:
            self.ctx.exit("XP", from_entry="L", qty=1, profit=3, loss=2)


def test_profit_loss_convert_to_limit_stop_exits():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 10, 10, 10),
        Bar(3, 10, 10, 10, 10),
        Bar(4, 10, 13, 9, 12),
    ]
    r = BacktestEngine(cfg()).run(BuyThenProfitLoss, bars=bars)
    assert r.closed_trades[0].exit_id == "XP:L"
    assert r.closed_trades[0].exit_price == 13


class BuyWithPendingExitThenReverse:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if self.ctx.state.position_size > 0:
            self.ctx.exit("XL", from_entry="L", qty=1, stop=5)
        if bar_index == 3:
            self.ctx.entry("S", "short", qty=1)


def test_opposite_entry_reverses_despite_pending_exit_reservation():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 10, 10, 10),
        Bar(3, 10, 10, 10, 10),
        Bar(4, 10, 10, 10, 10),
        Bar(5, 10, 10, 10, 10),
    ]
    r = BacktestEngine(cfg()).run(BuyWithPendingExitThenReverse, bars=bars)
    assert r.closed_trades
    assert r.closed_trades[0].entry_id == "L"
    assert r.closed_trades[0].exit_id == "S"
    assert r.open_trades
    assert r.open_trades[0].entry_id == "S"
    assert r.open_trades[0].direction == "short"


class RepricedExit:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if self.ctx.state.position_size > 0:
            self.ctx.exit("XL", from_entry="L", qty=1, stop=bar.close - 1)


def test_repeated_exit_id_reprices_existing_order():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 10, 10, 10),
        Bar(3, 12, 12, 11, 12),
        Bar(4, 12, 12, 10.5, 11),
        Bar(5, 11, 11, 9.5, 10),
    ]
    r = BacktestEngine(cfg(collect_events=True)).run(RepricedExit, bars=bars)
    created = [
        event for event in r.events or [] if event.code == "ORDER_CREATED" and event.order_id == "XL:S"
    ]
    modified = [
        event for event in r.events or [] if event.code == "ORDER_MODIFIED" and event.order_id == "XL:S"
    ]
    assert len(created) == 1
    assert modified


class ReuseEntryIdAfterReverse:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if bar_index == 1 and self.ctx.state.position_size > 0:
            self.ctx.exit("XL", from_entry="L", qty=1, stop=9)
        if bar_index == 2:
            self.ctx.entry("S", "short", qty=1)
        if bar_index == 4:
            self.ctx.entry("L", "long", qty=1)


def test_reverse_cancels_orphaned_exit_before_entry_id_is_reused():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 10, 10, 10),
        Bar(3, 10, 10, 10, 10),
        Bar(4, 10, 10, 10, 10),
        Bar(5, 10, 10, 10, 10),
        Bar(6, 10, 10, 8, 10),
    ]
    r = BacktestEngine(cfg(collect_events=True)).run(ReuseEntryIdAfterReverse, bars=bars)
    assert [trade.exit_id for trade in r.closed_trades] == ["S", "L"]
    assert r.open_trades
    assert r.open_trades[0].entry_id == "L"
    assert any(
        event.code == "ORDER_CANCELLED" and event.order_id == "XL:S" for event in r.events or []
    )


class StopRepricedAfterIntrabarHit:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)
        if self.ctx.state.position_size > 0:
            self.ctx.exit("XL", from_entry="L", qty=1, stop=bar.close - 1)


def test_active_stop_fills_before_strategy_reprices_it_on_same_bar():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 10, 10, 10),
        Bar(3, 12, 12, 8, 12),
    ]
    r = BacktestEngine(cfg()).run(StopRepricedAfterIntrabarHit, bars=bars)
    assert r.closed_trades
    assert r.closed_trades[0].exit_id == "XL:S"
    assert r.closed_trades[0].exit_price == 9


class TwoEntriesExitSecond:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L1", "long", qty=1)
            self.ctx.entry("L2", "long", qty=1)
        if bar_index == 2:
            self.ctx.exit("X2", from_entry="L2", qty=1, loss=1)
            self.ctx.exit("X1", from_entry="L1", qty=1, limit=20, stop=5)


def test_from_entry_matching_and_oca_reservation_do_not_fifo_close_wrong_entry():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 10, 10, 10),
        Bar(3, 10, 10, 10, 10),
        Bar(4, 10, 10, 9, 9),
    ]
    r = BacktestEngine(cfg(pyramiding=2)).run(TwoEntriesExitSecond, bars=bars)
    assert [t.entry_id for t in r.closed_trades] == ["L2"]
    assert r.open_trades[0].entry_id == "L1"
    # X1's bracket reserved only one unit as an OCA group, so it was accepted.
    assert not any(
        d.order_id == "X1" and d.code == "ORDER_REJECTED_NO_AVAILABLE_POSITION_QTY"
        for d in r.warnings
    )


def test_content_hash_excludes_execution_time():
    bars = [Bar(1, 10, 10, 10, 10), Bar(2, 10, 10, 10, 10), Bar(3, 10, 10, 10, 10)]
    r1 = BacktestEngine(cfg()).run(BuyThenTrailing, bars=bars)
    r2 = BacktestEngine(cfg()).run(BuyThenTrailing, bars=bars)
    assert r1.execution_time_ms != r2.execution_time_ms
    assert r1.content_hash() == r2.content_hash()


class RuntimeProbe:
    def __init__(self):
        self.begin = 0
        self.end = 0

    def begin_bar(self, bar, bar_index):
        self.begin += 1

    def end_bar(self):
        self.end += 1


class BuyAndDrop:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)


class ReserveFirstThenGlobalExit:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L1", "long", qty=1)
            self.ctx.entry("L2", "long", qty=1)
        if bar_index == 2:
            self.ctx.exit("X1", from_entry="L1", qty=1, limit=20, stop=5)
            self.ctx.exit("XALL", qty=2, loss=1)


class DoNothing:
    def __init__(self, params, runtime, ctx):
        pass

    def _process_bar(self, bar, bar_index):
        pass


def test_global_exit_does_not_consume_entry_reserved_by_specific_exit():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 10, 10, 10),
        Bar(3, 10, 10, 10, 10),
        Bar(4, 10, 10, 9, 9),
    ]
    r = BacktestEngine(cfg(pyramiding=2)).run(ReserveFirstThenGlobalExit, bars=bars)
    assert [(t.entry_id, t.exit_id, t.qty) for t in r.closed_trades] == [("L2", "XALL:S", 1.0)]
    assert [(t.entry_id, t.qty) for t in r.open_trades] == [("L1", 1.0)]


def test_unavailable_required_metrics_are_not_marked_available_on_flat_equity():
    bars = [Bar(1, 10, 10, 10, 10), Bar(2, 10, 10, 10, 10), Bar(3, 10, 10, 10, 10)]
    r = BacktestEngine(cfg(required_metrics={"sharpe", "sortino"})).run(DoNothing, bars=bars)
    assert r.sharpe_ratio is None and r.sortino_ratio is None
    assert "sharpe_ratio" not in r.available_outputs
    assert "sortino_ratio" not in r.available_outputs
    assert {d.code for d in r.errors} >= {"REQUIRED_METRIC_UNAVAILABLE"}


def test_early_stop_still_calls_end_bar_and_callback():
    runtime = RuntimeProbe()
    ended = []
    bars = [Bar(1, 10, 10, 10, 10), Bar(2, 10, 10, 1, 1)]
    cb = __import__("backtest_engine").BacktestCallbacks(on_bar_end=lambda *a: ended.append(a[1]))
    r = BacktestEngine(cfg(runtime=runtime, early_stop_enabled=True, min_equity_stop=9995)).run(
        BuyAndDrop, bars=bars, callbacks=cb
    )
    assert r.status == "early_stopped"
    assert runtime.begin == runtime.end == len(ended) == 2


def test_batch_thread_backend_and_required_metrics():
    bars = [
        Bar(1, 10, 10, 10, 10),
        Bar(2, 10, 11, 10, 11),
        Bar(3, 11, 11, 9, 9),
        Bar(4, 9, 12, 9, 12),
    ]
    c = cfg(required_metrics={"sharpe", "sortino"})
    out = BatchBacktestRunner(c, backend="thread", max_workers=2).run(
        [BacktestJob("a", BuyAndDrop, bars=bars), BacktestJob("b", BuyAndDrop, bars=bars)]
    )
    assert set(out) == {"a", "b"}
    assert out["a"].sharpe_ratio is not None
    assert "sharpe_ratio" in out["a"].available_outputs


def test_cli_benchmark_and_batch(tmp_path: Path):
    strat = tmp_path / "strat.py"
    bars = tmp_path / "bars.json"
    jobs = tmp_path / "jobs.json"
    strat.write_text(
        'class S:\n    def __init__(self, params, runtime, ctx): self.ctx=ctx\n    def _process_bar(self, bar, bar_index):\n        if bar_index==0: self.ctx.entry("L","long",qty=1)\n'
    )
    bars.write_text(
        json.dumps(
            [
                {"time": 1, "open": 10, "high": 10, "low": 10, "close": 10},
                {"time": 2, "open": 11, "high": 11, "low": 11, "close": 11},
            ]
        )
    )
    jobs.write_text(json.dumps([{"job_id": "j1", "params": {}}, {"job_id": "j2", "params": {}}]))
    bench_out = tmp_path / "bench.json"
    batch_out = tmp_path / "batch.json"
    assert (
        cli_main(
            [
                "benchmark",
                "--strategy",
                str(strat),
                "--class",
                "S",
                "--bars",
                str(bars),
                "--symbol",
                "S",
                "--timeframe",
                "1D",
                "--output",
                str(bench_out),
                "--runs",
                "1",
            ]
        )
        == 0
    )
    report = json.loads(bench_out.read_text())
    assert (
        report["bars_per_sec"] > 0
        and report["wall_time_sec"] >= 0
        and "peak_memory_bytes" in report
    )
    assert (
        cli_main(
            [
                "batch",
                "--strategy",
                str(strat),
                "--class",
                "S",
                "--bars",
                str(bars),
                "--jobs",
                str(jobs),
                "--symbol",
                "S",
                "--timeframe",
                "1D",
                "--output",
                str(batch_out),
                "--backend",
                "thread",
            ]
        )
        == 0
    )
    assert set(json.loads(batch_out.read_text())) == {"j1", "j2"}


def test_cli_batch_process_backend_loads_strategy_module(tmp_path: Path):
    strat = tmp_path / "process_strategy.py"
    bars = tmp_path / "bars.json"
    jobs = tmp_path / "jobs.json"
    out = tmp_path / "batch-process.json"
    strat.write_text(
        'class S:\n    def __init__(self, params, runtime, ctx): self.ctx=ctx\n    def _process_bar(self, bar, bar_index):\n        if bar_index==0: self.ctx.entry("L","long",qty=1)\n'
    )
    bars.write_text(
        json.dumps(
            [
                {"time": 1, "open": 10, "high": 10, "low": 10, "close": 10},
                {"time": 2, "open": 11, "high": 11, "low": 11, "close": 11},
            ]
        )
    )
    jobs.write_text(json.dumps([{"job_id": "p1", "params": {}}, {"job_id": "p2", "params": {}}]))
    assert (
        cli_main(
            [
                "batch",
                "--strategy",
                str(strat),
                "--class",
                "S",
                "--bars",
                str(bars),
                "--jobs",
                str(jobs),
                "--symbol",
                "S",
                "--timeframe",
                "1D",
                "--output",
                str(out),
                "--backend",
                "process",
                "--max-workers",
                "2",
            ]
        )
        == 0
    )
    assert set(json.loads(out.read_text())) == {"p1", "p2"}
