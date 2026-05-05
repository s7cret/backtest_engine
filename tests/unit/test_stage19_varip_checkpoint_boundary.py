from __future__ import annotations

from backtest_engine import BacktestConfig, BacktestEngine


class RuntimeWithVaripAwareExport:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def export_state(self, *, include_varip: bool = True) -> dict[str, object]:
        self.calls.append(include_varip)
        state: dict[str, object] = {"normal": 1}
        if include_varip:
            state["varip"] = 2
        return state

    def restore_state(self, state: object) -> None:
        del state


def test_realtime_execution_checkpoint_excludes_varip_from_runtime_snapshot() -> None:
    engine = BacktestEngine(
        BacktestConfig(symbol="TEST", timeframe="1", start_time=0, end_time=1, commission_type="none")
    )
    runtime = RuntimeWithVaripAwareExport()

    checkpoint = engine._export_realtime_execution_checkpoint(runtime=runtime)

    assert runtime.calls == [False]
    assert checkpoint.runtime_state == {"normal": 1}
