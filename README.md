# Backtest Engine

Independent Python package for deterministic bar-by-bar historical strategy execution on OHLCV bars.

The package exposes only its own dataclasses and `typing.Protocol` boundaries. It intentionally does not import PineLib, AST2Python, Pine2AST, or MarketDataProvider. Integrations belong in adapters.

## Quick start

```python
from backtest_engine import BacktestConfig, BacktestEngine, Bar

class BuyOnce:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx
    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)

bars = [
    Bar(1, 10, 11, 9, 10),
    Bar(2, 12, 13, 11, 12),
]
result = BacktestEngine(BacktestConfig(symbol="S", timeframe="1D", start_time=1, end_time=2)).run(BuyOnce, bars=bars)
```

Useful public helpers live under `backtest_engine.core` (clock/lifecycle/execution mode/state snapshots) and `backtest_engine.results` (drawdowns, equity returns, trade rows, metrics, comparison, writers).

Resume/checkpointing is exposed through `BacktestResumeState` and `core.BrokerSnapshot`: set `export_resume_state=True` and implement `export_state()`/`restore_state(state)` on strategy/runtime objects for continuation. Durable cross-process resume still requires caller-provided stable serializers.

See `docs/releases/` and `ROADMAP.md` for stage status and TZ compliance notes.
