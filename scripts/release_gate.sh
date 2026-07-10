#!/usr/bin/env bash
set -euo pipefail
PYTHON="${PYTHON:-python}"
"$PYTHON" -m compileall -q backtest_engine tests
"$PYTHON" -m ruff check .
"$PYTHON" -m mypy backtest_engine --no-incremental
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PYTHON" -m pytest -q -p pytest_cov tests --cov=backtest_engine --cov-report=term
"$PYTHON" -m backtest_engine.quality duplicates backtest_engine
"$PYTHON" -m backtest_engine.quality architecture backtest_engine --max-lines 700
"$PYTHON" -m backtest_engine.distribution manifest --root .
"$PYTHON" -m backtest_engine.release --root .
bash scripts/smoke_import_parse.sh
bash scripts/smoke_wheel_import.sh
