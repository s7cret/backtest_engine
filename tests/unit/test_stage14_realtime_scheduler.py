from __future__ import annotations

import pytest

from backtest_engine.core.realtime import build_bar_tick_schedule
from backtest_engine.errors import ConfigError
from backtest_engine.models import Bar, Tick


def test_tick_scheduler_assigns_ticks_to_explicit_bar_windows() -> None:
    bars = [
        Bar(time=100, open=1, high=2, low=1, close=2, time_close=160),
        Bar(time=160, open=2, high=3, low=2, close=3, time_close=220),
    ]
    ticks = [Tick(100, 1.0), Tick(159, 1.5), Tick(160, 2.0), Tick(219, 2.5)]

    schedule = build_bar_tick_schedule(bars, ticks)

    assert [s.bar_index for s in schedule] == [0, 1]
    assert [[t.time for t in s.ticks] for s in schedule] == [[100, 159], [160, 219]]


def test_tick_scheduler_uses_next_bar_time_when_time_close_missing() -> None:
    bars = [
        Bar(time=100, open=1, high=2, low=1, close=2),
        Bar(time=160, open=2, high=3, low=2, close=3),
    ]
    ticks = [Tick(120, 1.2), Tick(160, 2.0), Tick(999, 2.9)]

    schedule = build_bar_tick_schedule(bars, ticks)

    assert [[t.time for t in s.ticks] for s in schedule] == [[120], [160, 999]]


def test_tick_scheduler_rejects_unsorted_ticks() -> None:
    bars = [Bar(time=100, open=1, high=2, low=1, close=2, time_close=160)]

    with pytest.raises(ConfigError, match="sorted"):
        build_bar_tick_schedule(bars, [Tick(120, 1.2), Tick(110, 1.1)])


def test_tick_scheduler_rejects_ticks_outside_bar_windows() -> None:
    bars = [Bar(time=100, open=1, high=2, low=1, close=2, time_close=160)]

    with pytest.raises(ConfigError, match="outside available bar windows"):
        build_bar_tick_schedule(bars, [Tick(160, 1.6)])


def test_tick_scheduler_rejects_ticks_before_first_window() -> None:
    bars = [Bar(time=100, open=1, high=2, low=1, close=2, time_close=160)]

    with pytest.raises(ConfigError, match="before the current bar window"):
        build_bar_tick_schedule(bars, [Tick(99, 1.0)])
