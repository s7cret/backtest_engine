from __future__ import annotations

import importlib.util

import pytest

from backtest_engine.core.execution_backend_adapter import resolve_execution_backend
from backtest_engine.errors import ConfigError


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
