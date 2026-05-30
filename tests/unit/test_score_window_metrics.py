import pytest

from backtest_engine.models import EquityPoint, Trade
from backtest_engine.results import calculate_score_window_metrics


def _point(index: int, equity: float, drawdown: float, runup: float) -> EquityPoint:
    return EquityPoint(
        bar_index=index,
        time=index * 1_000,
        equity=equity,
        cash=equity,
        position_size=0.0,
        position_avg_price=None,
        open_profit=0.0,
        realized_profit=equity - 100.0,
        drawdown=drawdown,
        drawdown_percent=drawdown,
        runup=runup,
        runup_percent=runup,
    )


def _trade(exit_bar_index: int, profit: float) -> Trade:
    return Trade(
        id=f"t{exit_bar_index}",
        entry_id="entry",
        exit_id="exit",
        direction="long",
        entry_time=0,
        entry_bar_index=0,
        entry_price=100.0,
        exit_time=exit_bar_index * 1_000,
        exit_bar_index=exit_bar_index,
        exit_price=100.0 + profit,
        qty=1.0,
        commission_entry=0.0,
        commission_exit=0.0,
        profit=profit,
        profit_percent=profit,
    )


def test_calculate_score_window_metrics_filters_pre_score_trades() -> None:
    metrics = calculate_score_window_metrics(
        closed_trades=[_trade(2, 5.0), _trade(5, 10.0)],
        score_equity_points=[
            _point(3, 100.0, 0.0, 0.0),
            _point(4, 105.0, 0.0, 5.0),
            _point(5, 110.0, 0.0, 10.0),
        ],
        score_start_index=3,
    )

    assert metrics is not None
    assert metrics.total_trades == 1
    assert metrics.net_profit == pytest.approx(10.0)
    assert metrics.max_runup == pytest.approx(10.0)
    assert metrics.bars_processed == 3


def test_calculate_score_window_metrics_returns_none_without_score_points() -> None:
    assert (
        calculate_score_window_metrics(
            closed_trades=[],
            score_equity_points=[],
            score_start_index=0,
        )
        is None
    )
