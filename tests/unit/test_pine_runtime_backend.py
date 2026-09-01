from __future__ import annotations

import pytest

from backtest_engine.core.execution_backend_adapter import (
    ensure_executable_backend,
    resolve_execution_backend,
)
from backtest_engine.errors import ConfigError


class NativeBackend:
    def execute(self, *args: object, **kwargs: object) -> object:
        return object()


def test_rc6_execution_backend_accepts_explicit_backend_object() -> None:
    backend = NativeBackend()
    assert resolve_execution_backend(backend) is backend
    assert ensure_executable_backend(backend) is backend


def test_rc6_execution_backend_rejects_removed_pine_runtime_name() -> None:
    with pytest.raises(ConfigError, match="unknown execution backend"):
        resolve_execution_backend("pine_runtime")
