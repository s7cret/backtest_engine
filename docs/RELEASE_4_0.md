# Release 4.0.0

`backtest-engine` is prepared as part of the OpenPine 4.x package family.

## Scope

The package provides deterministic broker, order, fill, risk, equity, and result simulation for OpenPine backtests. External live services and sibling repositories are intentionally outside the default hermetic gate; run full OpenPine stack smoke tests before coordinated tags.

## Release gate

```bash
python -m compileall -q backtest_engine tests
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p pytest_cov tests --cov=backtest_engine --cov-report=term
python -m backtest_engine.quality duplicates backtest_engine
python -m backtest_engine.quality architecture backtest_engine --max-lines 700
python -m backtest_engine.distribution manifest --root .
python -m backtest_engine.release --root .
bash scripts/smoke_import_parse.sh
```

## Final hardening notes

- Coverage gate: 100%.
- Current measured coverage: 100.00%.
- Architecture budget: no Python module above 700 lines.
- Duplicate implementation groups: 0.
- Deterministic distribution builder excludes caches, build artifacts, coverage files, bytecode, and egg-info metadata.
- Added third-pass tests for broker/runtime lifecycle, generated-strategy bridge, execution backend adapters, reporting, batch/CLI behavior, and release/distribution edge cases.
- Fixed deterministic distribution file discovery when the selected root is not the current working directory.
