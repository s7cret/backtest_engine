# Architecture

`backtest_engine` is an independent OpenPine stack library. Core modules avoid hard runtime coupling to sibling repositories unless integration adapters explicitly need them.

The package exposes deterministic dataclass/protocol contracts and keeps network or sibling-repository behavior behind explicit tests, environment gates, or optional imports.


## Hardening layout

The second 4.0.0 pass keeps modules under the 700-line architecture budget and moves optional integration behavior behind standalone-tested boundaries. `backtest_engine` should remain importable and testable without sibling OpenPine repositories.
