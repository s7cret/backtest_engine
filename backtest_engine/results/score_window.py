from __future__ import annotations

from dataclasses import dataclass

from backtest_engine.models import EquityPoint, Trade
from backtest_engine.results.metrics import sharpe_ratio, sortino_ratio
from backtest_engine.results.statistics import summarize


@dataclass(frozen=True, slots=True)
class ScoreWindowMetrics:
    net_profit: float
    net_profit_percent: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    avg_trade: float
    sharpe_ratio: float | None
    sortino_ratio: float | None
    max_drawdown: float
    max_drawdown_percent: float
    max_runup: float
    max_runup_percent: float
    bars_processed: int


def calculate_score_window_metrics(
    *,
    closed_trades: list[Trade],
    score_equity_points: list[EquityPoint],
    score_start_index: int,
) -> ScoreWindowMetrics | None:
    if not score_equity_points:
        return None

    score_trades = [
        trade
        for trade in closed_trades
        if trade.exit_bar_index is not None
        and trade.exit_bar_index >= score_start_index
    ]
    score_initial_capital = score_equity_points[0].equity
    score_final_equity = score_equity_points[-1].equity
    score_stats = summarize(
        [trade.profit for trade in score_trades],
        score_initial_capital,
        score_final_equity,
    )

    score_sharpe_ratio: float | None = None
    score_sortino_ratio: float | None = None
    if len(score_equity_points) > 1:
        returns = [
            (score_equity_points[index].equity - score_equity_points[index - 1].equity)
            / score_equity_points[index - 1].equity
            for index in range(1, len(score_equity_points))
            if score_equity_points[index - 1].equity
        ]
        score_sharpe_ratio = sharpe_ratio(returns)
        score_sortino_ratio = sortino_ratio(returns)

    return ScoreWindowMetrics(
        net_profit=score_stats.get("net_profit", 0.0),
        net_profit_percent=score_stats.get("net_profit_percent", 0.0),
        total_trades=score_stats.get("total_trades", 0),
        winning_trades=score_stats.get("winning_trades", 0),
        losing_trades=score_stats.get("losing_trades", 0),
        win_rate=score_stats.get("win_rate", 0.0),
        profit_factor=score_stats.get("profit_factor", 0.0),
        avg_trade=score_stats.get("avg_trade", 0.0),
        sharpe_ratio=score_sharpe_ratio,
        sortino_ratio=score_sortino_ratio,
        max_drawdown=max(point.drawdown for point in score_equity_points),
        max_drawdown_percent=max(
            point.drawdown_percent for point in score_equity_points
        ),
        max_runup=max(point.runup for point in score_equity_points),
        max_runup_percent=max(point.runup_percent for point in score_equity_points),
        bars_processed=len(score_equity_points),
    )
