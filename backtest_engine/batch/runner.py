from dataclasses import replace
from backtest_engine.config import BacktestConfig
from backtest_engine.core import BacktestEngine
from backtest_engine.models import BacktestJob
class BatchBacktestRunner:
    def __init__(self, config: BacktestConfig): self.config=config
    def run(self,jobs:list[BacktestJob])->dict[str,object]:
        out={}
        for job in jobs:
            cfg=replace(self.config, **job.config_overrides)
            out[job.job_id]=BacktestEngine(cfg).run(job.strategy_class, params=job.params, bars=job.bars or cfg.preloaded_bars)
        return out
