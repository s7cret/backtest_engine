from __future__ import annotations

import json
import zipfile
from pathlib import Path

from backtest_engine import __version__
from backtest_engine.batch.shared_data import SharedBarCache
from backtest_engine.distribution import build_zip, distribution_manifest
from backtest_engine.models.bar_series import BarSeries
from backtest_engine.quality import (
    architecture_report,
    duplicate_report,
    main as quality_main,
)
from backtest_engine.release import main as release_main, release_report
from backtest_engine.reporting.console import render as render_console
from backtest_engine.reporting.monte_carlo_report import render as render_monte_carlo
from backtest_engine.reporting.summary import render as render_summary
from backtest_engine.results.monte_carlo import bootstrap_trade_profits
from backtest_engine.results.parity import ParityTolerance
from backtest_engine.results.tv_stream_compare import StreamingTradeComparator


def test_release_manifest_and_distribution_are_green(tmp_path: Path, capsys) -> None:
    assert __version__ == "4.0.0"
    report = release_report(Path.cwd())
    assert report.ok, report
    assert distribution_manifest(Path.cwd()).forbidden_count == 0
    assert duplicate_report("backtest_engine").duplicate_group_count == 0
    assert architecture_report("backtest_engine", max_lines=900).oversized_count == 0

    out = tmp_path / "release.json"
    assert release_main(["--root", ".", "--json", str(out)]) == 0
    assert json.loads(out.read_text())["ok"] is True

    assert quality_main(["architecture", "backtest_engine", "--max-lines", "900"]) == 0
    assert "oversized_count" in capsys.readouterr().out


def test_distribution_zip_builder_and_hygiene(tmp_path: Path) -> None:
    output = tmp_path / "backtest-engine-4.0.0.zip"
    build_zip(Path.cwd(), output, archive_root="backtest-engine-4.0.0")
    with zipfile.ZipFile(output) as zf:
        names = zf.namelist()
    assert "backtest-engine-4.0.0/pyproject.toml" in names
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)


def test_reporting_monte_carlo_parity_and_shared_cache() -> None:
    assert render_console("ok") == "ok\n"
    assert "final_equity" in render_summary(
        {"final_equity": 100, "net_profit": 1, "total_trades": 2}
    )
    runs = bootstrap_trade_profits([1.0, -0.5], initial_capital=100.0, runs=2, seed=1)
    assert len(runs) == 2
    assert "final_equity" in render_monte_carlo(runs)
    assert ParityTolerance(price=0.01, qty=0.1).price_equal(10.0, 10.005)

    comparator = StreamingTradeComparator(price_tolerance=0.0, qty_tolerance=0.0)
    comparator.add_actual(
        {"entry_id": "L", "entry_price": 1, "exit_price": 2, "qty": 1}
    )
    comparator.add_reference(
        {"entry_id": "L", "entry_price": 1, "exit_price": 2, "qty": 1}
    )
    assert comparator.report().matched

    cache = SharedBarCache()
    series = cache.put("a", [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1}])
    assert isinstance(series, BarSeries)
    assert cache.get_or_put("a", []) is series
    assert "a" in cache and len(cache) == 1 and list(cache) == ["a"]
    cache.clear()
    assert len(cache) == 0
