from __future__ import annotations
from dataclasses import replace
from typing import Literal
from backtest_engine.config import BacktestConfig
from backtest_engine.core import BacktestEngine
from backtest_engine.models import BacktestJob


def _run_job(config: BacktestConfig, job: BacktestJob) -> object:
    cfg=replace(config, **job.config_overrides)
    return BacktestEngine(cfg).run(job.strategy_class, params=job.params, bars=job.bars or cfg.preloaded_bars)


class BatchBacktestRunner:
    def __init__(self, config: BacktestConfig, backend: Literal['sequential','thread','process']='sequential', max_workers: int | None = None):
        self.config=config; self.backend=backend; self.max_workers=max_workers
    def run(self,jobs:list[BacktestJob])->dict[str,object]:
        if self.backend=='sequential':
            return {job.job_id:_run_job(self.config,job) for job in jobs}
        if self.backend=='thread':
            from backtest_engine.batch.thread_pool import run_thread_pool
            return run_thread_pool(self.config,jobs,self.max_workers)
        if self.backend=='process':
            from backtest_engine.batch.process_pool import run_process_pool
            return run_process_pool(self.config,jobs,self.max_workers)
        raise ValueError(f'unknown batch backend: {self.backend}')
