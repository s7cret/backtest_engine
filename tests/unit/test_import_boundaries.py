from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = ROOT / "backtest_engine"
FORBIDDEN_MARKETDATA_IMPORTS = (
    "marketdata_provider.core",
    "marketdata_provider.exchanges",
    "marketdata_provider.streaming",
    "marketdata_provider.timeframes",
)


def test_backtest_engine_does_not_import_marketdata_provider_implementations() -> None:
    offenders: list[str] = []
    for path in sorted(PRODUCTION_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(FORBIDDEN_MARKETDATA_IMPORTS):
                        offenders.append(path.relative_to(ROOT).as_posix())
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(FORBIDDEN_MARKETDATA_IMPORTS):
                    offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == []
