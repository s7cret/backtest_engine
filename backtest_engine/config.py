from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Literal
from .models.instrument import InstrumentModel


@dataclass
class BacktestConfig:
    symbol: str
    timeframe: str
    start_time: int
    end_time: int
    initial_capital: float = 10000.0
    currency: str = "USDT"
    default_qty_type: Literal["fixed", "percent_of_equity", "cash"] = "fixed"
    default_qty_value: float = 1.0
    pyramiding: int = 0
    allow_long: bool = True
    allow_short: bool = True
    reverse_on_opposite_entry: bool = True
    exit_matching: Literal["fifo", "lifo", "by_entry_id"] = "fifo"
    max_position_size: float | None = None
    margin_long: float = 100.0
    margin_short: float = 100.0
    unsupported_margin_policy: Literal["error", "warn", "ignore"] = "warn"
    instrument_model: InstrumentModel | None = None
    commission_type: Literal["percent", "fixed_per_order", "fixed_per_contract", "none"] = "percent"
    commission_value: float = 0.055
    slippage: float = 0.0
    slippage_type: Literal["tick", "price", "percent"] = "tick"
    mintick: float | None = None
    qty_step: float | None = None
    min_qty: float | None = None
    price_rounding: Literal["nearest", "floor", "ceil"] = "nearest"
    qty_rounding: Literal["nearest", "floor", "ceil"] = "floor"
    fill_model: Literal["tradingview_ohlc", "next_bar_open", "close_only"] = "tradingview_ohlc"
    parity_mode: Literal["tradingview", "strict", "custom"] = "tradingview"
    process_orders_on_close: bool = False
    calc_on_order_fills: bool = False
    max_recalc_depth: int = 10
    calc_on_every_tick: bool = False
    experimental_intrabar_strategy_mode: bool = False
    realtime_ticks: object | None = None
    realtime_tick_provider: object | None = None
    backtest_fill_limits_assumption_ticks: int = 0
    fill_worse_stop_at_path_price: bool = False
    limit_gap_fill_policy: Literal["tradingview", "limit_price", "open_price"] = "tradingview"
    stop_gap_fill_policy: Literal["open_price", "stop_price"] = "open_price"
    pyramiding_price_order_overfill_policy: Literal["tradingview", "strict"] = "tradingview"
    use_bar_magnifier: bool = False
    bar_magnifier_lower_tf: str | None = None
    bar_magnifier_missing_policy: Literal["error", "fallback"] = "error"
    missing_bar_policy: Literal["error", "warn", "ignore"] = "warn"
    duplicate_bar_policy: Literal["error", "keep_first", "keep_last"] = "error"
    validate_bars: bool = True
    max_bars_back: int = 0
    score_start_time: datetime | None = None
    score_end_time: datetime | None = None
    auto_pre_bars: bool = False
    min_pre_bars: int = 0
    max_pre_bars: int = 0
    warmup_confidence_mode: str = "unknown"
    data_source_kind: str = "BARS"
    execution_mode: Literal["debug", "normal", "fast", "ultra_fast"] = "normal"
    bar_storage_mode: Literal["object", "array", "auto"] = "auto"
    statistics_profile: Literal["minimal", "standard", "full"] = "standard"
    required_outputs: set[str] = field(
        default_factory=lambda: {"summary_metrics", "closed_trades", "open_trades"}
    )
    required_metrics: set[str] = field(default_factory=set)
    collect_events: bool = True
    collect_equity_curve: bool = True
    collect_trade_details: bool = True
    collect_mfe_mae: bool = True
    collect_order_lifecycle: bool = True
    callback_error_policy: Literal["raise", "diagnostic_continue", "disable_callbacks"] = "raise"
    content_hash_enabled: bool = True
    content_hash_include_equity_curve: bool = True
    content_hash_include_events: bool = False
    content_hash_algorithm: Literal["sha256"] = "sha256"
    data_fingerprint: str | None = None
    strategy_fingerprint: str | None = None
    runtime_fingerprint: str | None = None
    tradingview_reference_path: Path | None = None
    tradingview_compare_mode: Literal["off", "posthoc", "streaming"] = "off"
    tradingview_compare_stop_on_first_mismatch: bool = False
    tradingview_compare_tolerance_price: float = 0.0
    tradingview_compare_tolerance_qty: float = 0.0
    export_resume_state: bool = False
    resume_validation_policy: Literal["strict", "diagnostic"] = "strict"
    early_stop_enabled: bool = False
    max_drawdown_stop_percent: float | None = None
    min_equity_stop: float | None = None
    max_bars_without_trade: int | None = None
    force_close_on_end: bool = False
    strategy_config_priority: bool = True
    data_provider: object | None = None
    runtime: object | None = None
    preloaded_bars: object | None = None
    reuse_preloaded_bars: bool = True
    store_backtest_result_in_memory: bool = True
    output_dir: Path | None = None

    def snapshot(self) -> dict:
        d = asdict(self)
        d["data_provider"] = type(self.data_provider).__name__ if self.data_provider else None
        d["realtime_tick_provider"] = (
            type(self.realtime_tick_provider).__name__ if self.realtime_tick_provider else None
        )
        d["realtime_ticks"] = type(self.realtime_ticks).__name__ if self.realtime_ticks else None
        d["runtime"] = type(self.runtime).__name__ if self.runtime else None
        d["tradingview_reference_path"] = (
            str(self.tradingview_reference_path) if self.tradingview_reference_path else None
        )
        d["output_dir"] = str(self.output_dir) if self.output_dir else None
        d["required_outputs"] = sorted(self.required_outputs)
        d["required_metrics"] = sorted(self.required_metrics)
        return d
