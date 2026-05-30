import pytest

from backtest_engine.models import EquityPoint, Trade
from backtest_engine.results import (
    BacktestResult,
    apply_full_window_equity_extremes,
    apply_non_score_trade_metrics,
    mark_available_outputs,
)


def _trade(profit: float, bars_held: int | None = 1) -> Trade:
    return Trade(
        id=str(profit),
        entry_id="entry",
        exit_id="exit",
        direction="long",
        entry_time=0,
        entry_bar_index=0,
        entry_price=100.0,
        exit_time=1_000,
        exit_bar_index=1,
        exit_price=100.0 + profit,
        qty=1.0,
        commission_entry=0.1,
        commission_exit=0.2,
        profit=profit,
        profit_percent=profit,
        bars_held=bars_held,
    )


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


def test_apply_non_score_trade_metrics_sets_trade_stats_and_commission() -> None:
    result = BacktestResult()

    apply_non_score_trade_metrics(
        result,
        closed_trades=[_trade(5.0, 2), _trade(-3.0, 4)],
        open_trades=[_trade(0.0, None)],
        equity_curve=[
            _point(0, 100.0, 0.0, 0.0),
            _point(1, 105.0, 0.0, 5.0),
            _point(2, 103.0, 2.0, 5.0),
        ],
    )

    assert result.largest_win == pytest.approx(5.0)
    assert result.largest_loss == pytest.approx(3.0)
    assert result.avg_bars_in_trade == pytest.approx(3.0)
    assert result.commission_total == pytest.approx(0.9)
    assert result.sharpe_ratio is not None


def test_apply_full_window_equity_extremes_uses_curve_and_engine_extremes() -> None:
    result = BacktestResult()

    apply_full_window_equity_extremes(
        result,
        max_drawdown=2.0,
        max_drawdown_percent=2.0,
        max_runup=1.0,
        max_runup_percent=1.0,
        equity_curve=[_point(0, 100.0, 5.0, 3.0)],
    )

    assert result.max_drawdown == pytest.approx(5.0)
    assert result.max_drawdown_percent == pytest.approx(5.0)
    assert result.max_runup == pytest.approx(3.0)
    assert result.max_runup_percent == pytest.approx(3.0)


def test_mark_available_outputs_reflects_present_payloads() -> None:
    result = BacktestResult(closed_trades=[], equity_curve=[])

    mark_available_outputs(result)

    assert result.available_outputs == {
        "closed_trades",
        "equity_curve",
        "summary_metrics",
    }
