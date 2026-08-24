from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_mypy_gate_does_not_suppress_all_errors() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["tool"]["mypy"].get("ignore_errors", False) is False


def test_release_gate_runs_mypy_and_wheel_smoke_outside_checkout() -> None:
    release_gate = (ROOT / "scripts" / "release_gate.sh").read_text(encoding="utf-8")
    wheel_smoke_path = ROOT / "scripts" / "smoke_wheel_import.sh"

    assert '"$PYTHON" -m mypy backtest_engine --no-incremental' in release_gate
    assert "bash scripts/smoke_wheel_import.sh" in release_gate
    assert wheel_smoke_path.is_file()

    wheel_smoke = wheel_smoke_path.read_text(encoding="utf-8")
    assert "mktemp -d" in wheel_smoke
    assert 'cd "$SMOKE_ROOT"' in wheel_smoke
    assert '"$VENV/bin/python" -I' in wheel_smoke
    assert "site-packages" in wheel_smoke


def test_ci_uses_authoritative_release_gate_without_duplicate_weaker_checks() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert workflow.count("bash scripts/release_gate.sh") == 1
    assert "python -m mypy backtest_engine" not in workflow
    assert "Wheel import smoke" not in workflow


def test_ci_runs_feature_branches_once_via_pull_request_with_concurrency() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "push:\n    branches: [main]" in workflow
    assert "pull_request:\n    branches: [main]" in workflow
    assert "group: ci-${{ github.event.pull_request.number || github.ref }}" in workflow
    assert "cancel-in-progress: true" in workflow
