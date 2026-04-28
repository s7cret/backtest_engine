from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from backtest_engine.config import BacktestConfig
from backtest_engine.models import BacktestJob
from backtest_engine.batch.runner import _run_job


def run_thread_pool(
    config: BacktestConfig, jobs: list[BacktestJob], max_workers: int | None = None
) -> dict[str, object]:
    out: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_run_job, config, job): job.job_id for job in jobs}
        for fut in as_completed(futs):
            out[futs[fut]] = fut.result()
    return out
