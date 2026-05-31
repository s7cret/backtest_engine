"""Standalone BacktestConfig validation."""

from __future__ import annotations

from backtest_engine.config import BacktestConfig
from backtest_engine.errors import ConfigError


def validate_backtest_config(config: BacktestConfig) -> None:
    """Validate BacktestConfig before a run; raise ConfigError on problems."""
    if config.margin_long <= 0.0 or config.margin_short <= 0.0:
        raise ConfigError("margin_long and margin_short must be positive percentages")
    if (
        config.tradingview_compare_mode == "streaming"
        and config.execution_mode != "debug"
    ):
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
        raise ConfigError(
            "calc_on_every_tick tick replay is not implemented; realtime rollback/commit "
            "semantics must be oracle-verified before enabling execution"
        )
    if "equity_curve" in config.required_outputs and not config.collect_equity_curve:
        config.collect_equity_curve = True
    if (
        "order_lifecycle" in config.required_outputs
        or "order_events" in config.required_outputs
    ):
        config.collect_events = True
    if "mfe_mae" in config.required_outputs:
        config.collect_mfe_mae = True
        config.collect_trade_details = True
    if config.required_metrics:
        config.collect_equity_curve = True
