from __future__ import annotations

from typing import Any

import pytest

from backtest_engine import BacktestConfig, BacktestEngine, Bar
from backtest_engine.core.intent_replay import apply_intents_for_bar
from tests.unit.test_intent_replay import RecordingContext, _event


@pytest.mark.parametrize("kind", ["entry", "order", "exit", "cancel"])
def test_rc3_p0_002_replay_uses_order_id_instead_of_command_id(kind: str) -> None:
    """RC3-P0-002: canonical order identity comes from order_id."""
    ctx = RecordingContext()
    event = _event(
        0,
        kind=kind,
        command_id=f"command-{kind}",
        order_id=f"order-{kind}",
    )

    apply_intents_for_bar(ctx, [event], 2)

    assert ctx.calls[0][1][0] == f"order-{kind}"


def test_rc3_p0_002_exit_replay_preserves_converted_price_and_trailing_values() -> None:
    """RC3-P0-002: exit-only decimal fields survive the replay boundary."""
    ctx = RecordingContext()
    expected = {
        "profit": 1.25,
        "loss": 2.5,
        "trail_price": 10.025,
        "trail_points": 3.5,
        "trail_offset": 1.5,
    }
    event = _event(
        0,
        kind="exit",
        command_id="command-exit",
        order_id="order-exit",
        profit="1.25",
        loss="2.5",
        trail_price="10.025",
        trail_points="3.5",
        trail_offset="1.5",
    )

    apply_intents_for_bar(ctx, [event], 2)

    replayed = {name: ctx.calls[0][2].get(name) for name in expected}
    assert replayed == expected


class _CheckpointStrategy:
    def __init__(self, params: dict[str, Any], runtime: object, ctx: object) -> None:
        del params, runtime, ctx
        self.processed_bars: list[tuple[int, int]] = []

    def _process_bar(self, bar: Bar, bar_index: int) -> None:
        self.processed_bars.append((bar_index, bar.time))

    def export_state(self) -> dict[str, list[tuple[int, int]]]:
        return {"processed_bars": list(self.processed_bars)}

    def restore_state(self, state: dict[str, list[tuple[int, int]]]) -> None:
        self.processed_bars = list(state["processed_bars"])


_CHECKPOINT_BARS = [
    Bar(time=100 + index, open=10.0, high=11.0, low=9.0, close=10.0)
    for index in range(3)
]


def _checkpoint_config() -> BacktestConfig:
    return BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=_CHECKPOINT_BARS[0].time,
        end_time=_CHECKPOINT_BARS[-1].time,
        commission_type="none",
        early_stop_enabled=True,
        min_equity_stop=1_000_000_000.0,
        export_resume_state=True,
        semantic_profile="strict_5x",
        finality_policy="ALLOW_OPEN",
    )


def _early_stopped_result(bars: list[Bar]):
    return BacktestEngine(_checkpoint_config()).run(_CheckpointStrategy, bars=bars)


def test_rc3_p0_005_early_stop_checkpoint_identifies_last_processed_bar() -> None:
    """RC3-P0-005: result count and checkpoint cursor name the processed prefix."""
    result = _early_stopped_result(_CHECKPOINT_BARS)

    assert result.status == "early_stopped"
    assert result.resume_state is not None
    assert (result.bars_processed, result.resume_state.bar_index) == (1, 0)


def test_rc3_p0_005_early_stop_checkpoint_equals_exact_processed_prefix() -> None:
    """RC3-P0-005: unused suffix bars cannot change the exported engine checkpoint."""
    with_unused_suffix = _early_stopped_result(_CHECKPOINT_BARS)
    exact_prefix = _early_stopped_result(_CHECKPOINT_BARS[:1])

    assert with_unused_suffix.resume_state is not None
    assert exact_prefix.resume_state is not None
    assert with_unused_suffix.resume_state == exact_prefix.resume_state


def test_rc3_p1_backtest_config_defaults_to_strict_5x() -> None:
    """RC3-P1: strict_5x is the standalone engine's default semantic profile."""
    config = BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=100,
        end_time=101,
        finality_policy="ALLOW_OPEN",
    )

    assert config.semantic_profile == "strict_5x"
