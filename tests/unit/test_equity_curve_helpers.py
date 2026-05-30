from backtest_engine.results import equity_move_from_baseline, update_equity_extremes


def test_update_equity_extremes_tracks_streaming_drawdown_and_runup() -> None:
    first = update_equity_extremes(
        equity=110.0,
        peak_equity=100.0,
        trough_equity=100.0,
        max_drawdown=0.0,
        max_drawdown_percent=0.0,
        max_runup=0.0,
        max_runup_percent=0.0,
    )
    assert first.peak_equity == 110.0
    assert first.trough_equity == 100.0
    assert first.runup == 10.0
    assert first.max_runup == 10.0

    second = update_equity_extremes(
        equity=90.0,
        peak_equity=first.peak_equity,
        trough_equity=first.trough_equity,
        max_drawdown=first.max_drawdown,
        max_drawdown_percent=first.max_drawdown_percent,
        max_runup=first.max_runup,
        max_runup_percent=first.max_runup_percent,
    )
    assert second.peak_equity == 110.0
    assert second.trough_equity == 90.0
    assert second.drawdown == 20.0
    assert second.max_drawdown == 20.0
    assert second.max_runup == 10.0


def test_equity_move_from_baseline_preserves_intrabar_baseline_semantics() -> None:
    move = equity_move_from_baseline(
        baseline=100.0,
        adverse_equity=92.0,
        favorable_equity=115.0,
    )

    assert move.drawdown == 8.0
    assert move.drawdown_percent == 8.0
    assert move.runup == 15.0
    assert move.runup_percent == 15.0
