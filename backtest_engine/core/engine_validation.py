"""Standalone BacktestConfig validation."""

from __future__ import annotations

import math

from backtest_engine.config import BacktestConfig
from backtest_engine.errors import ConfigError


def validate_backtest_config(config: BacktestConfig) -> None:
    """Validate BacktestConfig before a run; raise ConfigError on problems."""
    for name in (
        "margin_long",
        "margin_short",
        "initial_capital",
        "default_qty_value",
        "commission_value",
        "slippage",
    ):
        value = getattr(config, name)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ConfigError(f"{name} must be a finite nonnegative number")
    for name in ("mintick", "qty_step", "min_qty"):
        value = getattr(config, name)
        if value is not None and (
            type(value) not in (int, float) or not math.isfinite(value) or value <= 0
        ):
            raise ConfigError(f"{name} must be positive and finite when supplied")
    if config.qty_rounding not in {"nearest", "floor", "ceil", "none", "truncate"}:
        raise ConfigError("unsupported qty_rounding mode")
    if config.price_rounding not in {"nearest", "floor", "ceil"}:
        raise ConfigError("unsupported price_rounding mode")
    if config.tradingview_compare_mode == "streaming" and config.execution_mode != "debug":
        raise ConfigError("streaming TradingView compare requires execution_mode=debug")
    if config.calc_on_every_tick:
        if not config.experimental_intrabar_strategy_mode:
            raise ConfigError(
                "calc_on_every_tick requires realtime rollback/varip semantics; "
                "BacktestEngine parity mode fails closed unless experimental_intrabar_strategy_mode=True"
            )
        if config.realtime_ticks is None and config.realtime_tick_provider is None:
            raise ConfigError(
                "calc_on_every_tick requires explicit realtime_ticks or realtime_tick_provider; "
                "historical OHLC fallback is forbidden"
            )
        if config.realtime_ticks is not None and config.realtime_tick_provider is not None:
            raise ConfigError(
                "calc_on_every_tick accepts exactly one tick source: realtime_ticks or "
                "realtime_tick_provider"
            )
    if "equity_curve" in config.required_outputs and not config.collect_equity_curve:
        config.collect_equity_curve = True
    if "order_lifecycle" in config.required_outputs or "order_events" in config.required_outputs:
        config.collect_events = True
    if "mfe_mae" in config.required_outputs:
        config.collect_mfe_mae = True
        config.collect_trade_details = True
    if config.required_metrics:
        config.collect_equity_curve = True
    from backtest_engine.core.warmup import SCORE_END_POLICIES, WARMUP_POLICIES

    if config.warmup_policy is not None and config.warmup_policy not in WARMUP_POLICIES:
        raise ConfigError(f"warmup_policy {config.warmup_policy!r} is unknown")
    if config.score_end_policy not in SCORE_END_POLICIES:
        raise ConfigError(f"score_end_policy {config.score_end_policy!r} is unknown")
    from openpine_contracts import SemanticProfile

    try:
        SemanticProfile(str(config.semantic_profile))
    except ValueError as exc:
        raise ConfigError(f"semantic_profile {config.semantic_profile!r} is unknown") from exc
