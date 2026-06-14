# Development

Run the local release gate from a clean checkout:

```bash
bash scripts/release_gate.sh
```

Equivalent expanded checks:

```bash
python -m compileall -q backtest_engine tests
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p pytest_cov tests --cov=backtest_engine --cov-report=term
python -m backtest_engine.quality duplicates backtest_engine
python -m backtest_engine.quality architecture backtest_engine --max-lines 700
python -m backtest_engine.distribution manifest --root .
python -m backtest_engine.release --root .
bash scripts/smoke_import_parse.sh
```

The 4.0.0 hardening gate requires 100% package coverage and no Python module above 700 lines. Network/live-provider and sibling-repository checks are intentionally outside the default gate and should be run as OpenPine integration smoke tests.
