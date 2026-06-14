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
