from __future__ import annotations

from backtest_engine.models import EquityPoint, Trade
from backtest_engine.results.metrics import sharpe_ratio, sortino_ratio
from backtest_engine.results.result import BacktestResult


def apply_non_score_trade_metrics(
    result: BacktestResult,
    *,
    closed_trades: list[Trade],
    open_trades: list[Trade],
    equity_curve: list[EquityPoint] | None,
) -> None:
    wins = [trade.profit for trade in closed_trades if trade.profit > 0]
    losses = [trade.profit for trade in closed_trades if trade.profit < 0]
    result.largest_win = max(wins) if wins else 0.0
    result.largest_loss = abs(min(losses)) if losses else 0.0

    held = [trade.bars_held for trade in closed_trades if trade.bars_held is not None]
    result.avg_bars_in_trade = sum(held) / len(held) if held else 0.0
    result.commission_total = sum(
        trade.commission_entry + trade.commission_exit for trade in closed_trades
    ) + sum(trade.commission_entry + trade.commission_exit for trade in open_trades)

    if equity_curve and len(equity_curve) > 1:
        equity_returns = [
            (equity_curve[index].equity - equity_curve[index - 1].equity)
            / equity_curve[index - 1].equity
            for index in range(1, len(equity_curve))
            if equity_curve[index - 1].equity
        ]
        result.sharpe_ratio = sharpe_ratio(equity_returns)
        result.sortino_ratio = sortino_ratio(equity_returns)


def apply_full_window_equity_extremes(
    result: BacktestResult,
    *,
    max_drawdown: float,
    max_drawdown_percent: float,
    max_runup: float,
    max_runup_percent: float,
    equity_curve: list[EquityPoint] | None,
) -> None:
    result.max_drawdown = max(
        [max_drawdown] + ([point.drawdown for point in equity_curve] if equity_curve else [])
    )
    result.max_drawdown_percent = max(
        [max_drawdown_percent]
        + ([point.drawdown_percent for point in equity_curve] if equity_curve else [])
    )
    result.max_runup = max(
        [max_runup] + ([point.runup for point in equity_curve] if equity_curve else [])
    )
    result.max_runup_percent = max(
        [max_runup_percent]
        + ([point.runup_percent for point in equity_curve] if equity_curve else [])
    )


def mark_available_outputs(result: BacktestResult) -> None:
    if result.closed_trades is not None:
        result.available_outputs.add("closed_trades")
    if result.open_trades is not None:
        result.available_outputs.add("open_trades")
    if result.equity_curve is not None:
        result.available_outputs.add("equity_curve")
    if result.events is not None:
        result.available_outputs.add("order_events")
    result.available_outputs.add("summary_metrics")
