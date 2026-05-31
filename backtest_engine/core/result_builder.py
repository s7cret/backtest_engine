"""Build BacktestResult objects from completed engine state."""

from __future__ import annotations

from typing import Any

from backtest_engine.core.score_window import build_phase_trades, classify_warmup_quality
from backtest_engine.core.validation import data_fingerprint
from backtest_engine.models import BarSeries, EquityPoint
from backtest_engine.results import (
    BacktestResult,
    apply_full_window_equity_extremes,
    apply_non_score_trade_metrics,
    calculate_score_window_metrics,
    mark_available_outputs,
)
from backtest_engine.results.statistics import summarize


def build_backtest_result(
    engine: Any,
    series: BarSeries,
    equity_curve: list[EquityPoint] | None,
    status: str,
    reason: str | None,
    execution_time_ms: float,
    strategy: Any | None = None,
    runtime: Any | None = None,
) -> BacktestResult:
    profits = [trade.profit for trade in engine.closed_trades]
    stats = summarize(profits, engine.config.initial_capital, engine.equity)
    result = BacktestResult(
        trades=(
            engine.closed_trades + engine.open_trades
            if engine.config.collect_trade_details
            else None
        ),
        closed_trades=(
            engine.closed_trades
            if engine._want("closed_trades") or engine.config.collect_trade_details
            else None
        ),
        open_trades=(
            engine.open_trades
            if engine._want("open_trades") or engine.config.collect_trade_details
            else None
        ),
        equity_curve=equity_curve,
        available_outputs=set(),
        initial_capital=engine.config.initial_capital,
        final_equity=engine.equity,
        bars_processed=len(series),
        execution_time_ms=execution_time_ms,
        status=status,
        early_stop_reason=reason,
        config_snapshot=engine.config.snapshot(),
        warnings=engine.warnings,
        errors=engine.errors,
        events=engine.events
        if engine.config.collect_events or engine._want("order_events")
        else None,
        data_fingerprint=engine.config.data_fingerprint or data_fingerprint(series),
        strategy_fingerprint=engine.config.strategy_fingerprint,
        runtime_fingerprint=engine.config.runtime_fingerprint,
    )

    if engine._score_mode and engine._bar_phases:
        phase_trades = build_phase_trades(
            closed_trades=engine.closed_trades,
            bar_phases=engine._bar_phases,
        )
        result.phase_trades = phase_trades or None
    else:
        result.phase_trades = None

    for key, value in stats.items():
        setattr(result, key, value)
    result.max_drawdown = engine.max_drawdown
    result.max_drawdown_percent = engine.max_drawdown_percent
    result.max_runup = engine.max_runup
    result.max_runup_percent = engine.max_runup_percent

    if engine._score_mode and engine._score_equity_points:
        _apply_score_window_metrics(engine, result)
    else:
        apply_non_score_trade_metrics(
            result,
            closed_trades=engine.closed_trades,
            open_trades=engine.open_trades,
            equity_curve=equity_curve,
        )

    _apply_required_metric_availability(engine, result)
    if not engine._score_mode:
        apply_full_window_equity_extremes(
            result,
            max_drawdown=engine.max_drawdown,
            max_drawdown_percent=engine.max_drawdown_percent,
            max_runup=engine.max_runup,
            max_runup_percent=engine.max_runup_percent,
            equity_curve=equity_curve,
        )
    plots = _strategy_plot_records(strategy, runtime)
    if plots is not None:
        result.plots = plots
        result.available_outputs.add("plots")
    mark_available_outputs(result)
    if engine.config.export_resume_state:
        result.resume_state = engine._export_resume_state(len(series) - 1, strategy, runtime)
    if engine.config.content_hash_enabled:
        result.content_hash_value = result.content_hash(
            engine.config.content_hash_include_equity_curve,
            engine.config.content_hash_include_events,
        )

    recommended_raw = (
        engine.config.warmup_metadata.get("recommended_pre_bars_raw", 0)
        if engine.config.warmup_metadata
        else 0
    )
    result.warmup = classify_warmup_quality(
        bar_phases=engine._bar_phases,
        effective_pre_bars=getattr(engine, "_effective_pre_bars", None),
        recommended_pre_bars_raw=recommended_raw,
        requested_max_pre_bars=engine.config.max_pre_bars,
    )

    return result


def _strategy_plot_records(strategy: Any | None, runtime: Any | None) -> Any | None:
    """Return plot records captured by Pine-backed native strategies, if any."""

    for candidate in (runtime, getattr(strategy, "_pine_runtime", None)):
        recorder = getattr(candidate, "plot_recorder", None)
        if recorder is None:
            continue
        get_records = getattr(recorder, "get_records", None)
        return get_records() if callable(get_records) else recorder
    return None


def _apply_score_window_metrics(engine: Any, result: BacktestResult) -> None:
    score_metrics = calculate_score_window_metrics(
        closed_trades=engine.closed_trades,
        score_equity_points=engine._score_equity_points,
        score_start_index=engine._score_start_index,
    )
    if score_metrics is None:
        return
    result.score_net_profit = score_metrics.net_profit
    result.score_net_profit_percent = score_metrics.net_profit_percent
    result.score_total_trades = score_metrics.total_trades
    result.score_winning_trades = score_metrics.winning_trades
    result.score_losing_trades = score_metrics.losing_trades
    result.score_win_rate = score_metrics.win_rate
    result.score_profit_factor = score_metrics.profit_factor
    result.score_avg_trade = score_metrics.avg_trade
    result.score_sharpe_ratio = score_metrics.sharpe_ratio
    result.score_sortino_ratio = score_metrics.sortino_ratio
    result.score_max_drawdown = score_metrics.max_drawdown
    result.score_max_drawdown_percent = score_metrics.max_drawdown_percent
    result.score_max_runup = score_metrics.max_runup
    result.score_max_runup_percent = score_metrics.max_runup_percent
    result.bars_processed = score_metrics.bars_processed


def _apply_required_metric_availability(engine: Any, result: BacktestResult) -> None:
    for metric in engine.config.required_metrics:
        if metric == "sharpe":
            if result.sharpe_ratio is not None:
                result.available_outputs.add("sharpe_ratio")
        elif metric == "sortino":
            if result.sortino_ratio is not None:
                result.available_outputs.add("sortino_ratio")
        else:
            engine._diag(
                "REQUIRED_METRIC_UNSUPPORTED",
                f"required metric {metric} is unsupported",
                "error",
            )
    if "sharpe" in engine.config.required_metrics and result.sharpe_ratio is None:
        engine._diag(
            "REQUIRED_METRIC_UNAVAILABLE",
            "sharpe requires at least two non-constant equity returns",
            "error",
        )
    if "sortino" in engine.config.required_metrics and result.sortino_ratio is None:
        engine._diag(
            "REQUIRED_METRIC_UNAVAILABLE",
            "sortino requires at least one downside return",
            "error",
        )
