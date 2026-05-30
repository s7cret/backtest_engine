from .comparison import ComparisonReport, compare_trades, load_trades_csv
from .content_hash import result_content_hash
from .drawdown import DrawdownPoint, calculate_drawdowns, max_drawdown, max_drawdown_from_curve
from .equity_curve import (
    EquityExtremes,
    EquityMove,
    EquityCurveSummary,
    equity_move_from_baseline,
    equity_point,
    equity_values,
    final_equity,
    returns,
    summarize_equity_curve,
    update_equity_extremes,
)
from .metrics import sharpe_ratio, sortino_ratio, summary_metrics, trade_profits
from .result import BacktestResult
from .finalize import (
    apply_full_window_equity_extremes,
    apply_non_score_trade_metrics,
    mark_available_outputs,
)
from .score_window import ScoreWindowMetrics, calculate_score_window_metrics
from .trade_log import closed_trade_rows, trade_to_row, trades_to_rows
from .writers import CSVTradeWriter, JSONResultWriter

__all__ = [
    "BacktestResult",
    "JSONResultWriter",
    "CSVTradeWriter",
    "ComparisonReport",
    "compare_trades",
    "load_trades_csv",
    "result_content_hash",
    "DrawdownPoint",
    "calculate_drawdowns",
    "max_drawdown",
    "max_drawdown_from_curve",
    "equity_values",
    "equity_point",
    "summarize_equity_curve",
    "EquityExtremes",
    "EquityMove",
    "EquityCurveSummary",
    "equity_move_from_baseline",
    "update_equity_extremes",
    "returns",
    "final_equity",
    "trade_profits",
    "summary_metrics",
    "apply_full_window_equity_extremes",
    "apply_non_score_trade_metrics",
    "mark_available_outputs",
    "sharpe_ratio",
    "sortino_ratio",
    "ScoreWindowMetrics",
    "calculate_score_window_metrics",
    "trade_to_row",
    "trades_to_rows",
    "closed_trade_rows",
]
