# Backtest Engine 4.0.0

> Independent deterministic bar-by-bar strategy backtest engine for OpenPine-generated strategies and Python strategy classes.

[![Version](https://img.shields.io/badge/version-4.0.0-blue)](https://github.com/s7cret/backtest_engine) [![Python](https://img.shields.io/badge/python-%3E%3D3.11-blue)](https://github.com/s7cret/backtest_engine) [![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/s7cret/backtest_engine)


**GitHub description:** Backtest Engine is the deterministic broker, order, fill, position, trade, equity, and reporting authority for OpenPine backtests.

**Suggested topics:** `backtesting`, `algorithmic-trading`, `pine-script`, `tradingview`, `broker-emulator`, `quant`, `python`, `openpine`.

## What Backtest Engine is

Backtest Engine is the execution and accounting layer of the OpenPine stack. It consumes normalized bars and a strategy class, then produces deterministic orders, fills, trades, equity curves, diagnostics, exports, and comparison artifacts.

```text
Pine source -> pine2ast -> ast2python -> pinelib -> backtest-engine -> results
                                             │
marketdata-provider -> normalized bars ------┘
```

The package is intentionally runtime-focused. It does not parse Pine, download market data, generate Python code, or optimize parameters by itself.

## Responsibilities

- Bar-by-bar strategy execution over validated OHLCV input.
- Market, limit, stop, stop-limit, bracket, reversal, and close order handling.
- Fill simulation and broker-like position/trade ledger ownership.
- Commission, slippage, pyramiding, margin/risk diagnostics, and equity tracking.
- Backtest windows, prehistory/warmup metadata, resume state, and batch execution helpers.
- Result export to JSON/CSV/Markdown-friendly structures.
- TradingView comparison helpers for validating exported runs.

## Boundaries

| In scope | Out of scope |
|---|---|
| Broker/fill/equity authority | Pine source parsing |
| Strategy lifecycle over bars | Pine AST lowering/code generation |
| Result models and exports | Exchange data fetching |
| Batch/benchmark/compare helpers | Parameter search orchestration |
| Optional generated-strategy bridge | Live trading order routing |

## Install

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Install from GitHub tag:

```bash
python -m pip install 'git+https://github.com/s7cret/backtest_engine.git@v4.0.0'
```

## Python quick start

```python
from backtest_engine import BacktestConfig, BacktestEngine, Bar

bars = [
    Bar(1, 10, 11, 9, 10),
    Bar(2, 10, 12, 9, 11),
    Bar(3, 11, 12, 8, 9),
    Bar(4, 9, 10, 7, 8),
]

class BuyOnce:
    def __init__(self, params, runtime, ctx):
        self.ctx = ctx

    def _process_bar(self, bar, bar_index):
        if bar_index == 0:
            self.ctx.entry("L", "long", qty=1)

config = BacktestConfig(
    symbol="BTCUSDT",
    timeframe="1D",
    start_time=1,
    end_time=4,
    commission_type="none",
)

result = BacktestEngine(config).run(BuyOnce, bars=bars)
print(result.open_trades)
print(result.closed_trades)
```

## CLI quick start

```bash
backtest run   --strategy generated_strategy.py   --class GeneratedStrategy   --bars bars.json   --symbol BTCUSDT   --timeframe 15m   --start 1704067200000   --end 1706745600000   --capital 10000   --output result.json
```

Other CLI surfaces:

```bash
backtest compare --our result.json --tv tradingview.csv --output compare.json
backtest export --input result.json --trades-csv trades.csv --summary-md summary.md
backtest benchmark --strategy generated_strategy.py --class GeneratedStrategy --bars bars.json --symbol BTCUSDT --timeframe 15m --output bench.json
backtest batch --strategy generated_strategy.py --class GeneratedStrategy --bars bars.json --param-grid params.json --symbol BTCUSDT --timeframe 15m --output batch.json
```

## Repository layout

```text
backtest_engine/
  broker/                 order book, fill simulator, commission/slippage helpers
  core/                   BacktestEngine and validation lifecycle
  models/                 bars, orders, fills, positions, trades, diagnostics
  results/                result models and writers
  batch/                  batch runner and job model
  cli/                    run/compare/export/benchmark/batch commands
  adapters/               optional generated-strategy/runtime adapters
  tests/                  unit, integration, contract, and release-gate tests
```

## Release checks

```bash
python -m compileall -q backtest_engine tests
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p pytest_cov tests --cov=backtest_engine --cov-report=term
python -m backtest_engine.quality duplicates backtest_engine
python -m backtest_engine.quality architecture backtest_engine --max-lines 700
python -m backtest_engine.distribution manifest --root .
python -m backtest_engine.release --root .
bash scripts/smoke_import_parse.sh
```

## Trading disclaimer

Backtests are research tools. They depend on the correctness of input data, strategy implementation, execution assumptions, fees, slippage, and market conditions. A successful backtest does not guarantee future performance.

## Documentation

- `docs/ARCHITECTURE.md` — runtime responsibilities and hardening layout.
- `docs/DEVELOPMENT.md` — local checks and release workflow.
- `docs/RELEASE_4_0.md` — 4.0.0 scope and release gate.

## License

MIT. See `LICENSE`.

## Support

OpenPine development is independent and MIT-licensed. Support is optional and does not change license terms, feature access, or project guarantees.

- Telegram: https://t.me/OpenPine
- TON: `UQAyIr2sQ4-_Q5L-4VINcU18khDas5GPbAlYEkQN6S_qzui2`
- SOL: `EbxMUK2W4RGeQZCTRFrdgpEJvnqtyczPZvBrQa1cYJnQ`