from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal, Any
from backtest_engine.models import (
    BacktestResumeState,
    Diagnostic,
    EquityPoint,
    ExecutionWindow,
    PrehistoryPlan,
    Trade,
    TradeResult,
    WarmupQuality,
)
from backtest_engine.core.deterministic_hash import sha256_obj


@dataclass
class BacktestResult:
    execution_window: ExecutionWindow | None = None
    prehistory_plan: PrehistoryPlan | None = None
    warmup: WarmupQuality | None = None
    trades: list[Trade] | None = None
    phase_trades: list[TradeResult] | None = None
    closed_trades: list[Trade] | None = None
    open_trades: list[Trade] | None = None
    equity_curve: list[EquityPoint] | None = None
    plots: Any | None = None
    # Raw per-bar results from execution backend (list of BackendBarResult or similar)
    bar_results: list[Any] | None = None
    available_outputs: set[str] = field(default_factory=set)
    initial_capital: float = 0.0
    final_equity: float = 0.0
    net_profit: float = 0.0
    net_profit_percent: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_percent: float = 0.0
    max_runup: float = 0.0
    max_runup_percent: float = 0.0
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_trade: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    avg_bars_in_trade: float = 0.0
    commission_total: float = 0.0
    expectancy: float = 0.0
    return_drawdown_ratio: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    bars_processed: int = 0
    # D5-E: Score-window metrics — set when score mode is active
    score_net_profit: float = 0.0
    score_net_profit_percent: float = 0.0
    score_total_trades: int = 0
    score_winning_trades: int = 0
    score_losing_trades: int = 0
    score_win_rate: float = 0.0
    score_profit_factor: float = 0.0
    score_max_drawdown: float = 0.0
    score_max_drawdown_percent: float = 0.0
    score_max_runup: float = 0.0
    score_max_runup_percent: float = 0.0
    score_avg_trade: float = 0.0
    score_sharpe_ratio: float | None = None
    score_sortino_ratio: float | None = None
    execution_time_ms: float = 0.0
    status: Literal["completed", "failed", "early_stopped"] = "completed"
    early_stop_reason: str | None = None
    config_snapshot: dict = field(default_factory=dict)
    performance: dict = field(default_factory=dict)
    warnings: list[Diagnostic] = field(default_factory=list)
    errors: list[Diagnostic] = field(default_factory=list)
    events: list[Diagnostic] | None = None
    content_hash_value: str | None = None
    data_fingerprint: str | None = None
    strategy_fingerprint: str | None = None
    runtime_fingerprint: str | None = None
    resume_state: BacktestResumeState | None = None

    def content_hash(self, include_equity_curve: bool = True, include_events: bool = False) -> str:
        payload = asdict(self)
        for key in ("content_hash_value", "execution_time_ms", "performance"):
            payload.pop(key, None)
        if not include_equity_curve:
            payload.pop("equity_curve", None)
        if not include_events:
            payload.pop("events", None)
        return sha256_obj(payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
