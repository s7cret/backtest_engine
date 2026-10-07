# Typed JSON resume for native bar runs

`JsonResumeStateSerializer` implements the existing `StateSerializer` protocol.
It exports a complete engine-owned `BacktestResumeState` into UTF-8 JSON bytes
and reconstructs the engine models needed by strict native bar restore:

```python
from pathlib import Path
from backtest_engine import BacktestEngine, JsonResumeStateSerializer

producer = BacktestEngine(config)  # config.export_resume_state must be True
result = producer.run(Strategy, bars=bars[:cut])
Path("checkpoint.json").write_bytes(
    JsonResumeStateSerializer().dumps(result.resume_state)
)
# The producer can exit here. A new process uses the same config and bar prefix.
consumer = BacktestEngine(config)
continued = consumer.run(
    Strategy, bars=bars, resume_state=Path("checkpoint.json").read_bytes()
)
```

The consuming strategy must implement the existing export/restore contract when
strategy state is present. Primitive dictionaries, lists, tuples, strings, booleans,
integers, finite floats, and null are supported in strategy/runtime state. Foreign
objects need a codec supplied by their owner. No checkpoint-provided import name
is resolved and no pickle loader is used.

The envelope has exactly `schema`, `version`, and `state` fields. Its schema is
`backtest-engine.resume`, version is integer `1`, and serializer ID is
`json-resume-v1`. The closed model registry contains `BacktestResumeState`,
`BrokerSnapshot`, `Position`, `Order`, `Fill`, `Trade`, `EquityPoint`, and
`Diagnostic`. Each model carries a `type` tag and all its `fields`; unknown or
missing fields and types are rejected. User mappings have a separate `mapping`
wrapper, so an ordinary user key named `type` never selects a model.

Admission rejects duplicate JSON keys, non-finite numbers, scalar coercions such
as bool quantities or floating-point indices, invalid enums, incomplete nested
state, invalid indices and fill links, inconsistent trade statistics, and invalid
risk/exit-template data. Existing strict restore still checks the strategy/runtime
contracts and restores their state through its rollback path.

Native fill history is reconciled through durable opening fill indices and closing
trade identities, quantities, and commission allocations. Terminal orders may have
been pruned with `collect_order_lifecycle=False`; forced-close and margin-call
orders can be ephemeral. These are supported without accepting an unknown fill
identity disconnected from the trade graph. Position direction/quantity/average,
cash/equity, realized/open profit, fees, and equity-point accounting are checked
using the native instrument, commission, mark rounding, and dust-threshold owners.

New exports include a closed `engine-accounting-v1` context under the reserved
`metadata.native_accounting` key. It carries producer accounting parameters and
the committed mark, and strict public restore binds it to the current config and
bar prefix. Complete legacy checkpoints without this context are checked against
the consuming config. History checks compare bounded input lengths/iterators;
an untrusted cursor never allocates an expected array of that cursor's size.

For bytes passed to `BacktestEngine.run`, decoding and complete payload admission
occur before callbacks are replaced or the live engine is reset. Config identity,
available cursor, processed bar fingerprint, required equity history, and score
window are also admitted before reset. Contextual config/prefix mismatches retain
the existing lenient-policy warning behavior if that policy is explicitly chosen;
schema and payload validation always remain strict.

Hard ceilings are 16 MiB of input/output, nesting depth 64 (including wire JSON
wrappers), 200,000 visited values/keys, and 4096 bits per integer. Optional
`max_bytes`, `max_depth`, and `max_items` constructor arguments can lower these
bounds; they cannot raise or disable them. Export also applies a conservative
allocation budget before producing JSON. Cycles and non-string mapping keys are
rejected.

`JsonStateSerializer` remains the compatible primitive `json-v1` serializer; its
dataclass mappings do not become typed restore checkpoints automatically. Existing
in-memory typed resume remains available. Realtime tick snapshots, foreign
execution backends, full-job atomic cuts, and protected-worker orchestration are
outside this codec's native committed-bar scope.
