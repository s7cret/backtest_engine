from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from backtest_engine.core.execution_backend_adapter import resolve_execution_backend
from backtest_engine.errors import ConfigError


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "module_name",
    [
        "backtest_engine.adapters.generated_strategy",
        "backtest_engine.adapters.generated_strategy_context",
        "backtest_engine.adapters.generated_strategy_errors",
        "backtest_engine.execution_backends.pine_runtime",
    ],
)
def test_rc6_distribution_has_no_legacy_generated_runtime_modules(
    module_name: str,
) -> None:
    assert importlib.util.find_spec(module_name) is None


def test_rc6_rejects_removed_pine_runtime_backend_alias() -> None:
    with pytest.raises(ConfigError, match="unknown execution backend"):
        resolve_execution_backend("pine_runtime")


def test_public_protocols_do_not_advertise_the_removed_generated_bridge() -> None:
    protocols = (ROOT / "backtest_engine" / "protocols.py").read_text(encoding="utf-8")

    assert "class PineRuntime" not in protocols
    assert "class GeneratedStrategy" not in protocols
    assert "_process_bar" not in protocols


def test_readme_documents_run_bar_without_the_removed_generated_bridge() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "def run_bar" in readme
    assert "_process_bar" not in readme
    assert "generated-strategy bridge" not in readme.lower()
