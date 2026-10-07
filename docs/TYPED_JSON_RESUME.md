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
`json-resume-v1`. The closed model registry contains `BacktestResumeState`, `Bar`,
`BrokerSnapshot`, `Position`, `Order`, `Fill`, `Trade`, `EquityPoint`, and
`Diagnostic`. Each model carries a `type` tag and all its `fields`; unknown or
missing fields and types are rejected. User mappings have a separate `mapping`
wrapper, so an ordinary user key named `type` never selects a model.
`Finality` has its own fixed enum tag and only the statically imported `OPEN` and
`FINAL` values. `Bar` requires all fields, exact integer timestamps, finite OHLCV,
valid high/low bounds, nonnegative optional volume, and ordered optional close
time. These additive tags are rejected by older readers that do not know them;
the envelope version and existing native bar wire format remain unchanged.

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
the consuming config, including its native dust threshold. Without that context,
decoding retains structural ledger checks and defers only dust-dependent quantity
comparisons until public admission; those checks run before reset for both strict
and lenient consumers. Strict restore binds the mark tick to the consumer's actual
effective tick, including inference when `config.mintick` is absent, rather than
using the checkpoint's own tick as its identity anchor.
With `mintick=None`, adding more precise future prices can change inferred tick
size and therefore reject strict continuation. Use an explicit stable `mintick`
when the stream's future precision can differ. The checkpoint cannot choose its
own rounding anchor to bypass that check.
History checks compare bounded input lengths/iterators;
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
in-memory typed resume remains available.

A trusted caller-selected backend can implement `prepare_native_execution` for
the historical native loop. Pass it through the existing
`BacktestEngine.run(..., execution_backend=backend, resume_state=bytes)` API.
The engine first applies the same typed transport, accounting, config and bar
prefix admission as native bytes restore, then calls the backend's complete
owner preflight with a detached typed checkpoint, the admitted series, selected
strategy class, parameters and callbacks. It must return `PreparedNativeExecution`
with the native strategy class, parameters and callbacks before any live reset.
Incomplete results and owner failures reject before replacement. The prepared
strategy uses the existing native loop and export/restore contracts. JSON cannot
select this backend, a Python import or an alternate model registry. Execute-only
foreign backends still reject bytes; this contract currently excludes ticks.
It is a local owner adapter, not protected-worker or full-job resume acceptance.

Committed explicit-tick restore is also available for callers that supply trusted
owners with complete pure preflight. The checkpoint must carry
`metadata.realtime_resume_boundary="committed-parent-bar-v1"`, a committed input
cursor, runtime and strategy state, and the processed tick schedule fingerprint.
The exporter stamps this boundary only outside an active tick/bar attempt.
Public bytes restore requires strict policy and explicit list/tuple `Tick` input;
provider-driven streams and provisional tick snapshots are not admitted.

The strategy class declares `realtime_resume_runtime = "config"`. Both that class
and the configured runtime class implement a static or class method:

```python
@staticmethod
def validate_resume_state(state, *, bar_index, committed_bar):
    # Fully check this owner's closed state schema, mandatory typed slots,
    # committed cursor/bar identity and any late nested fields; raise on failure.
    # Return None without mutating an owner or constructing/restoring live state.
    ...
```

These are trusted application methods, selected from classes already supplied by
the caller. JSON cannot select a class, add a validator, or import a module. The
engine passes detached state to preflight before reset, baseline export/restore,
strategy construction or callbacks. It does not infer a runtime schema from user
mapping keys. An owner without this explicit contract fails closed. A successful
preflight must guarantee the owner's restore accepts the admitted schema; the
existing restore rollback path still handles unexpected owner restore errors.

The consumer verifies config, effective tick, accounting and bar prefix before
owner preflight, freezes the explicit tick input, verifies native OHLCV schedule
reconstruction and the processed tick prefix, then reuses that schedule once for
execution. Unknown/partial schemas, generic mappings in required typed slots,
provisional/abort state forbidden by the owner, and late nested corruption are
rejected before live state changes.

Foreign generated checkpoints remain owned by their existing generated-session
and PineLib checkpoint admission code. This codec does not fork those schemas or
admit them merely because their contents are primitive mappings. Foreign
execution backends, full-job atomic cuts, and protected-worker orchestration
remain outside this bounded native scope.
