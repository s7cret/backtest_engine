from __future__ import annotations
import time, tracemalloc
from dataclasses import dataclass, asdict
from typing import Any
from backtest_engine.config import BacktestConfig
from backtest_engine.core import BacktestEngine

@dataclass
class BenchmarkReport:
    runs:int; bars:int; total_bars:int; wall_time_sec:float; bars_per_sec:float; peak_memory_bytes:int|None
    def to_dict(self)->dict[str,Any]: return asdict(self)


def run_benchmark(config:BacktestConfig, strategy_class:type, *, bars:object, params:dict|None=None, runs:int=1)->BenchmarkReport:
    params=params or {}; total_bars=0
    tracemalloc.start(); t0=time.perf_counter()
    try:
        for _ in range(runs):
            r=BacktestEngine(config).run(strategy_class, params=params, bars=bars)
            total_bars+=r.bars_processed
        _,peak=tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    wall=time.perf_counter()-t0
    return BenchmarkReport(runs=runs,bars=total_bars//runs if runs else 0,total_bars=total_bars,wall_time_sec=wall,bars_per_sec=(total_bars/wall if wall else 0.0),peak_memory_bytes=peak)
