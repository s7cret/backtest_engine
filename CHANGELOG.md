# Changelog

## 4.0.0

- Primary release-candidate cleanup for the OpenPine 4.x package family.
- Added deterministic release/distribution/quality gates.
- Added `python -m backtest_engine` entrypoint.
- Tightened standalone test behavior and archive hygiene.
- Fixed first-pass runtime/resource-management issues found during review.
- Split or constrained large modules so the architecture budget stays under 700 lines.
- Raised the default coverage gate to 100%.
- Added hermetic third-pass tests for broker/runtime lifecycle, generated-strategy bridge, execution backend adapters, reporting, batch/CLI behavior, and release/distribution edge cases.
- Fixed deterministic distribution path filtering for non-current roots.
- Updated README and canonical docs to match the actual release gate.
- **Pine v5/v6 `na` semantics**: `_is_pine_na(float('nan'))` now returns `True`.  Pre-4.0 the helper treated NaN as a regular float; 4.0 aligns with `math.isnan` and Pine Script's `na` sentinel, so generated strategies that compute with `na` produce identical behaviour in backtest and in TradingView.
- **Test package namespaces**: `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/contracts/__init__.py` are now shipped so `from tests.unit import …` style imports work when the venv has pinelib installed editable (the pre-4.0 design relied on the absence of pinelib in sys.modules, which is not the case for normal development).
