"""Required owner commit is a trusted phase before optional user publication."""

import pytest
from openpine_contracts import Finality

from backtest_engine import BacktestCallbacks, BacktestConfig, BacktestEngine, Bar


@pytest.mark.parametrize("failing_callback", ["on_bar_start", "on_equity", "on_bar_end"])
def test_native_required_owner_commits_even_when_user_callbacks_are_disabled(failing_callback):
    commits, optional_events = [], []

    class Strategy:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def run_bar(self, bar, index):
            pass

        def _commit_bar(self, index):
            commits.append(index)

        def export_state(self):
            return {"commits": list(commits)}

    def fail(*args):
        optional_events.append(args)
        raise ValueError("optional subscriber failed")

    config = BacktestConfig(
        "S",
        "1m",
        0,
        120000,
        callback_error_policy="disable_callbacks",
        force_close_on_end=False,
        export_resume_state=True,
    )
    bars = [Bar(i * 60000, 100, 100, 100, 100, 1, finality=Finality.FINAL) for i in range(3)]
    engine = BacktestEngine(config)
    result = engine.run(
        Strategy,
        bars=bars,
        callbacks=BacktestCallbacks(**{failing_callback: fail}),
    )
    assert result.status == "completed" and engine._callbacks_disabled
    assert commits == [0, 1, 2] and len(optional_events) == 1
    assert result.resume_state.strategy_state == {"commits": [0, 1, 2]}


def test_required_owner_failure_prevents_optional_bar_publication():
    publications = []

    class Strategy:
        def __init__(self, params, runtime, ctx):
            pass

        def run_bar(self, bar, index):
            pass

        def _commit_bar(self, index):
            raise ValueError("required owner commit failed")

    engine = BacktestEngine(
        BacktestConfig(
            "S",
            "1m",
            0,
            0,
            callback_error_policy="disable_callbacks",
        )
    )
    with pytest.raises(ValueError, match="required owner commit failed"):
        engine.run(
            Strategy,
            bars=[Bar(0, 100, 100, 100, 100, 1, finality=Finality.FINAL)],
            callbacks=BacktestCallbacks(on_bar_end=lambda *args: publications.append(args)),
        )
    assert publications == [] and not engine._callbacks_disabled
