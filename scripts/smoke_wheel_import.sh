#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python}"
SMOKE_ROOT="$(mktemp -d)"
VENV="$SMOKE_ROOT/venv"
DIST_DIR="$SMOKE_ROOT/dist"
trap 'rm -rf "$SMOKE_ROOT"' EXIT

"$PYTHON" -m build --wheel --outdir "$DIST_DIR" "$ROOT"
shopt -s nullglob
wheels=("$DIST_DIR"/*.whl)
if (( ${#wheels[@]} != 1 )); then
    printf 'expected exactly one wheel in %s, found %s\n' "$DIST_DIR" "${#wheels[@]}" >&2
    exit 1
fi

"$PYTHON" -m venv "$VENV"
env -u PYTHONPATH "$VENV/bin/python" -m pip install "${wheels[0]}" --quiet
cd "$SMOKE_ROOT"
"$VENV/bin/python" -I -c "import pathlib, backtest_engine; path = pathlib.Path(backtest_engine.__file__).resolve(); assert 'site-packages' in path.parts, path; print(path, backtest_engine.__version__)"
