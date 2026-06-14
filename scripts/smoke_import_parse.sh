#!/usr/bin/env bash
set -euo pipefail
PYTHON="${PYTHON:-python}"
"$PYTHON" - <<'PY'
import backtest_engine
print(backtest_engine.__name__, getattr(backtest_engine, "__version__", "unknown"))
PY
