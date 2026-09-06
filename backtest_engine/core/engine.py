from __future__ import annotations

import time
from typing import Any, Literal, cast

from backtest_engine.broker.commission import calculate_commission
from backtest_engine.broker.rounding import round_to_step
from backtest_engine.config import BacktestConfig
from backtest_engine.context import StrategyContext, StrategyStateView
from backtest_engine.core.engine_realtime import EngineRealtimeMixin
from backtest_engine.core.engine_support import EngineSupportMixin
from backtest_engine.core.engine_validation import validate_backtest_config
from backtest_engine.core.execution_backend_adapter import run_execution_backend
from backtest_engine.core.fill_execution import execute_fill
from backtest_engine.core.fill_scanner import process_bar_fills, update_trailing_order
from backtest_engine.core.finality import admit_bars
from backtest_engine.core.margin_call import maybe_margin_call
from backtest_engine.core.native_run_loop import run_native_strategy
from backtest_engine.core.oca import apply_oca
from backtest_engine.core.position_accounting import apply_position
from backtest_engine.core.price_path import (
    infer_parent_close,
    limit_fill_price,
    price_path,
    validate_lower_timeframe_bars,
    validate_supplied_bar_magnifier_bars,
)
from backtest_engine.core.protocol_boundary import (
    prepare_protocol_run,
)
from backtest_engine.core.risk_rules import (
    apply_risk_rules,
    max_position_size_allows,
    pending_entry_position_delta,
)
from backtest_engine.core.score_window import (
    build_score_window_plan,
)
from backtest_engine.core.strategy_command_processor import flush_strategy_commands
from backtest_engine.core.strategy_projection import (
    build_strategy_ledger_projection,
    compact_broker_projection,
    compact_ledger_projection,
)
from backtest_engine.core.validation import infer_price_tick, validate_bars
from backtest_engine.errors import (
    BarMagnifierUnavailableError,
    ProviderError,
)
from backtest_engine.ledger.runup_drawdown import trade_excursion_values
from backtest_engine.models import (
    BacktestCallbacks,
    BacktestResumeState,
    Bar,
    BarSeries,
    Diagnostic,
    EquityPoint,
    Fill,
    InstrumentModel,
    Order,
    Position,
    Trade,
)
from backtest_engine.results import (
    BacktestResult,
    equity_move_from_baseline,
)


class BacktestEngine(EngineSupportMixin, EngineRealtimeMixin):
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.instrument = config.instrument_model or InstrumentModel()
        self.callbacks = BacktestCallbacks()
        self._callbacks_disabled = False
        self._reset_state()

    def _reset_state(self) -> None:
        self.position = Position()
        self.cash = self.config.initial_capital
        self.equity = self.config.initial_capital
        self.peak_equity = self.config.initial_capital
        self.trough_equity = self.config.initial_capital
        self.max_drawdown = 0.0
        self.max_drawdown_percent = 0.0
        self.max_runup = 0.0
        self.max_runup_percent = 0.0
        self._allow_long = self.config.allow_long
        self._allow_short = self.config.allow_short
        self._early_stop_enabled = self.config.early_stop_enabled
        self._min_equity_stop = self.config.min_equity_stop
        self._max_drawdown_stop_percent = self.config.max_drawdown_stop_percent
        self._max_drawdown_stop_cash: float | None = None
        self._max_bars_without_trade = self.config.max_bars_without_trade
        self._max_position_size = self.config.max_position_size
        self._last_processed_bar_index = -1
        self.orders: list[Order] = []
        self.fills: list[Fill] = []
        self.closed_trades: list[Trade] = []
        self.open_trades: list[Trade] = []
        self._filled_exit_entry_keys: set[tuple] = set()
        self._all_entry_exits: dict[str, dict] = {}
        self._closed_trade_stats_count = 0
        self._gross_profit_total = 0.0
        self._gross_loss_total = 0.0
        self._win_trades_total = 0
        self._loss_trades_total = 0
        self._even_trades_total = 0
        self.events: list[Diagnostic] = []
        self.warnings: list[Diagnostic] = []
        self.errors: list[Diagnostic] = []
        self._score_equity_points: list[EquityPoint] = []
        self.state = StrategyStateView(
            initial_capital=self.config.initial_capital,
            cash=self.config.initial_capital,
            equity=self.config.initial_capital,
            _open_trades_ref=self.open_trades,
            _closed_trades_ref=self.closed_trades,
        )
        self.last_trade_bar: int | None = None
        self._effective_mintick: float | None = self.config.mintick
        # D5-C: score window state
        self._score_mode: bool = False
        self._prehistory_end_index: int = 0  # last prehistory bar index (inclusive)
        self._score_start_index: int = 0  # first score bar index
        self._bar_phases: list[str] = []  # "prehistory" or "score" per bar index
        self.warmup_phase: Any = None
        self.score_opening_broker: Any = None
        self.warmup_boundary_buffer_len_before: int | None = None
        self.warmup_boundary_buffer_len_after: int | None = None
        self.last_strategy: Any = None
        self._current_phase: str | None = None
        self._warmup_machine: Any = None
        self._strategy_callback_bar_index: int | None = None
        self._strategy_callback_recalc_iteration = -1
        self._recalc_budget_bar = None
        self._recalc_budget_used = 0
        self._execution_callback_sequence = -1
        self._execution_last_bar_index = -1
        self._execution_event = None

    def run(
        self,
        strategy_class: type,
        params: dict | None = None,
        bars: BarSeries | list[Bar] | None = None,
        callbacks: BacktestCallbacks | None = None,
        resume_state: BacktestResumeState | None = None,
        effective_pre_bars: int | None = None,
        execution_backend: Any | None = None,
        runtime_kwargs: dict[str, Any] | None = None,
        execution_context: dict[str, Any] | None = None,
        bar_envelopes: list[dict[str, Any]] | None = None,
    ) -> BacktestResult:
        t0 = time.perf_counter()
        params = params or {}
        self.callbacks = callbacks or BacktestCallbacks()
        self._callbacks_disabled = False
        self._reset_state()
        self._validate_config()
        from backtest_engine.core.warmup import admit_warmup_strategy

        admit_warmup_strategy(self.config.warmup_policy, strategy_class)
        series = self._resolve_bars(bars)
        self._effective_mintick = self.config.mintick or infer_price_tick(series)
        series = self._slice_range(series)
        prepare_protocol_run(self, execution_context, bar_envelopes, series)

        plan = build_score_window_plan(
            series_len=len(series),
            score_start_time=self.config.score_start_time,
            score_end_time=self.config.score_end_time,
            effective_pre_bars=effective_pre_bars,
        )
        self._score_mode = plan.score_mode
        self._prehistory_end_index = plan.prehistory_end_index
        self._score_start_index = plan.score_start_index
        self._bar_phases = list(plan.bar_phases)
        effective_pre_bars = plan.effective_pre_bars
        self._effective_pre_bars = effective_pre_bars

        if self.config.validate_bars:
            series, _ = validate_bars(series, self.config.duplicate_bar_policy)
        self._execution_last_bar_index = len(series) - 1
        if self.config.use_bar_magnifier and (
            not self.config.bar_magnifier_lower_tf
            or self.config.bar_magnifier_bars is None
        ):
            raise BarMagnifierUnavailableError(
                "bar magnifier lower timeframe bars unavailable"
            )
        if self.config.use_bar_magnifier and self.config.bar_magnifier_bars is not None:
            self._validate_supplied_bar_magnifier_bars(series)
        if execution_backend is not None:
            return self._run_execution_backend(
                execution_backend,
                strategy_class,
                params,
                series,
                t0,
                effective_pre_bars or 0,
                runtime_kwargs,
            )
        return run_native_strategy(
            self,
            strategy_class,
            params,
            series,
            t0,
            resume_state,
        )

    def process_next_bar(
        self,
        strategy_class: type,
        bar: Bar,
        *,
        params: dict | None = None,
        resume_state: BacktestResumeState | None = None,
        execution_backend: Any | None = None,
    ) -> BacktestResult:
        """Process one closed bar through the native BacktestEngine path.

        This public live/paper hook intentionally reuses ``run(...)`` with a
        one-bar series and optional resume_state, so OpenPine does not create a
        second Pine/order execution engine for single-bar processing.
        """
        return self.run(
            strategy_class,
            params=params,
            bars=[bar],
            resume_state=resume_state,
            execution_backend=execution_backend,
        )

    def export_strategy_ledger_projection(self) -> dict[str, Any]:
        """Return the canonical detached StrategyLedgerView worker payload."""

        return build_strategy_ledger_projection(self)

    def export_broker_projection(self) -> dict[str, Any]:
        """Return a detached compact broker/account projection."""

        return compact_broker_projection(self.export_strategy_ledger_projection())

    def export_ledger_projection(self) -> dict[str, Any]:
        """Return detached order/fill/trade ledgers without exposing engine objects."""

        return compact_ledger_projection(self.export_strategy_ledger_projection())

    def _validate_config(self) -> None:
        validate_backtest_config(self.config)

    def _slice_range(self, series: BarSeries) -> BarSeries:
        start = self.config.start_time
        end = self.config.end_time
        idx = [
            i for i, t in enumerate(series.time) if int(t) >= start and int(t) <= end
        ]
        if not idx:
            return BarSeries([], [], [], [], [], [], [], [])
        first = max(0, idx[0] - max(0, self.config.max_bars_back))
        last = idx[-1] + 1
        return BarSeries(
            series.time[first:last],
            series.open[first:last],
            series.high[first:last],
            series.low[first:last],
            series.close[first:last],
            None if series.volume is None else series.volume[first:last],
            None if series.time_close is None else series.time_close[first:last],
            None if series.finality is None else series.finality[first:last],
        )

    def _resolve_bars(self, bars: BarSeries | list[Bar] | None) -> BarSeries:
        if bars is None:
            raise ProviderError(
                "No bars supplied; load market data outside BacktestEngine"
            )
        admitted = admit_bars(bars, policy=self.config.finality_policy)
        if isinstance(admitted, BarSeries):
            return admitted
        return BarSeries.from_bars(admitted)

    def _call_strategy(self, strategy: Any, bar: Bar, i: int, *, fill_cause: bool = False) -> None:
        from backtest_engine.core.strategy_callback import call_strategy

        call_strategy(self, strategy, bar, i, fill_cause=fill_cause)

    def _run_execution_backend(
        self,
        execution_backend: Any,
        strategy_class: type,
        params: dict,
        series: BarSeries,
        t0: float,
        effective_pre_bars: int,
        runtime_kwargs: dict[str, Any] | None = None,
    ) -> BacktestResult:
        return run_execution_backend(
            self,
            execution_backend,
            strategy_class,
            params,
            series,
            t0,
            effective_pre_bars,
            runtime_kwargs,
        )

    def _flush(
        self,
        ctx: StrategyContext,
        bar: Bar,
        i: int,
        *,
        recalc_after_fill: bool = False,
    ) -> None:
        flush_strategy_commands(self, ctx, bar, i, recalc_after_fill=recalc_after_fill)

    def _apply_risk_rules(self, ctx: StrategyContext) -> None:
        apply_risk_rules(self, ctx)

    def _entry_direction_allowed(self, direction: str) -> bool:
        if direction == "long" and not self._allow_long:
            return False
        if direction == "short" and not self._allow_short:
            return False
        return True

    def _qty_from_args(
        self, kw: dict, current_size: float | None, price: float
    ) -> float:
        if kw.get("qty") is not None:
            q = float(kw["qty"])
        elif kw.get("qty_percent") is not None and current_size is not None:
            q = abs(current_size) * float(kw["qty_percent"]) / 100.0
        elif self.config.default_qty_type == "fixed":
            q = self.config.default_qty_value
        elif self.config.default_qty_type == "cash":
            q = self.config.default_qty_value / price
        else:
            notional = self.equity * self.config.default_qty_value / 100.0
            # TradingView percent-of-equity sizing reserves percent commission in
            # the notional denominator, so the entry plus entry commission fits
            # inside the requested equity percentage. Cash sizing does not do
            # this adjustment in the observed stock oracle cases.
            denom = price
            if self.config.commission_type == "percent":
                denom = price * (1.0 + self.config.commission_value / 100.0)
            q = notional / denom
        q = round_to_step(q, self.config.qty_step, self.config.qty_rounding)
        if self.config.min_qty and q < self.config.min_qty:
            q = 0.0
        return abs(q)

    def _entry_allowed(self, direction: str) -> bool:
        max_same_direction_entries = max(1, self.config.pyramiding)
        existing_orders = sum(
            1
            for o in self.orders
            if o.kind == "entry"
            and o.direction == direction
            and o.status in ("pending", "active")
        )
        active = sum(1 for t in self.open_trades if t.direction == direction)
        return active + existing_orders < max_same_direction_entries

    def _pending_entry_position_delta(
        self, exclude_order: Order | None = None
    ) -> float:
        return pending_entry_position_delta(self.orders, exclude_order=exclude_order)

    def _risk_allows_order(
        self, o: Order, bar: Bar, i: int, exclude_order: Order | None = None
    ) -> bool:
        if not max_position_size_allows(
            max_position_size=self._max_position_size,
            current_size=self.position.size,
            orders=self.orders,
            order=o,
            exclude_order=exclude_order,
        ):
            self._diag(
                "ORDER_REJECTED_RISK_MAX_POSITION_SIZE",
                "order rejected by risk.max_position_size",
                "warning",
                i,
                bar.time,
                o.id,
            )
            return False
        return True

    def _add_order(self, o: Order, bar: Bar, i: int) -> None:
        if o.qty <= 0:
            self._diag(
                "ORDER_REJECTED_ZERO_QTY",
                "order qty is zero",
                "warning",
                i,
                bar.time,
                o.id,
            )
            return
        if not self._risk_allows_order(o, bar, i):
            return
        if o.active_from_bar_index <= i:
            o.status = "active"
        self.orders.append(o)
        self._event("ORDER_CREATED", f"order {o.id} created", i, bar.time, o.id)
        self._cb("on_order_created", o)

    def _matching_open_trades(self, from_entry: str | None) -> list[Trade]:
        return [
            t
            for t in self.open_trades
            if from_entry is None or t.entry_id == from_entry
        ]

    def _reserved_qty_by_entry(
        self, exclude_order: Order | None = None
    ) -> dict[str, float]:
        from backtest_engine.core.exit_scope import reserved_by_trade

        allocation = reserved_by_trade(self, exclude_order)
        result: dict[str, float] = {}
        for trade in self.open_trades:
            if amount := allocation.get(id(trade), 0.0):
                result[trade.entry_id] = result.get(trade.entry_id, 0.0) + amount
        return result

    def _reserved_exit_qty(
        self, from_entry: str | None, exclude_order: Order | None = None
    ) -> float:
        by_entry = self._reserved_qty_by_entry(exclude_order)
        return sum(by_entry.values()) if from_entry is None else by_entry.get(from_entry, 0.0)

    def _available_exit_qty(
        self, from_entry: str | None, exclude_order: Order | None = None,
        *, entry_fill_index: int | None = None,
    ) -> float:
        from backtest_engine.core.exit_scope import matching_trades, reserved_by_trade

        reserved = reserved_by_trade(self, exclude_order)
        return sum(max(0.0, trade.qty - reserved.get(id(trade), 0.0))
                   for trade in matching_trades(self, from_entry, entry_fill_index))

    def _exit_base_price(self, from_entry: str | None) -> float:
        trades = self._matching_open_trades(from_entry)
        qty = sum(t.qty for t in trades)
        return (
            (sum(t.entry_price * t.qty for t in trades) / qty)
            if qty
            else self.position.avg_price
        )

    def _update_trailing_order(self, o: Order, price: float) -> None:
        update_trailing_order(o, price)

    def _process_bar_fills(
        self,
        strategy: Any,
        ctx: StrategyContext,
        bar: Bar,
        i: int,
        open_only: bool = False,
        skip_open: bool = False,
        close_activation_only: bool = False,
        skip_trailing: bool = False,
        trailing_only: bool = False,
        tick_phase: Literal["non_final", "final"] | None = None,
    ) -> None:
        process_bar_fills(
            self,
            strategy,
            ctx,
            bar,
            i,
            open_only=open_only,
            skip_open=skip_open,
            close_activation_only=close_activation_only,
            skip_trailing=skip_trailing,
            trailing_only=trailing_only,
            tick_phase=tick_phase,
        )

    def _maybe_margin_call(self, price: float, bar: Bar, i: int, point: str) -> bool:
        return maybe_margin_call(self, price, bar, i, point)

    def _limit_fill_price(
        self, o: Order, path_price: float, is_open_point: bool
    ) -> float:
        return limit_fill_price(self, o, path_price, is_open_point)

    def _price_path(self, bar: Bar) -> list[tuple[float, str]]:
        return price_path(self, bar)

    def _validate_lower_timeframe_bars(
        self, lower_series: BarSeries, parent: Bar
    ) -> None:
        """Fail closed on malformed bar-magnifier data before using intrabars."""
        validate_lower_timeframe_bars(self, lower_series, parent)

    def _validate_supplied_bar_magnifier_bars(self, series: BarSeries) -> None:
        validate_supplied_bar_magnifier_bars(self, series)

    def _infer_parent_close(self, parent_open: int) -> int:
        return infer_parent_close(self, parent_open)

    def _fill(self, o: Order, bar: Bar, i: int, price: float, point: str) -> None:
        execute_fill(self, o, bar, i, price, point)

    def _apply_position(
        self,
        o: Order,
        price: float,
        bar: Bar,
        i: int,
        commission: float,
        fill_point: str = "",
    ) -> str:
        return apply_position(self, o, price, bar, i, commission, fill_point=fill_point)

    def _update_trade_excursions(self, bar: Bar) -> None:
        if not self.config.collect_mfe_mae:
            return
        tick = getattr(self, "_effective_mintick", None) or self.config.mintick
        mark_close = round_to_step(bar.close, tick, "nearest") if tick else bar.close
        for tr in self.open_trades:
            tr.mfe, tr.mae, tr.max_runup, tr.max_drawdown = (
                self._trade_excursion_values(tr, bar)
            )
            tr.profit = (
                self.instrument.pnl(tr.entry_price, mark_close, tr.qty, tr.direction)
                - tr.commission_entry
            )
            self._cb("on_trade_update", tr)

    def _trade_excursion_values(
        self, tr: Trade, bar: Bar
    ) -> tuple[float, float, float, float]:
        return trade_excursion_values(tr, bar, self.instrument)

    def _apply_oca(self, o: Order, bar: Bar, i: int) -> None:
        apply_oca(self, o, bar, i)

    def _force_close(self, bar: Bar, i: int) -> None:
        direction = cast(Literal["long", "short"], self.position.direction)
        o = Order(
            "forced_end_close",
            "close",
            direction,
            "sell" if direction == "long" else "buy",
            "close",
            "market",
            abs(self.position.size),
            i,
            bar.time,
            i,
            direction,
            True,
            immediately=True,
        )
        self._fill(o, bar, i, bar.close, "close")

    def _update_open_profit(self, price: float) -> None:
        tick = getattr(self, "_effective_mintick", None) or self.config.mintick
        mark_price = round_to_step(price, tick, "nearest") if tick else price
        self.position.open_profit = (
            0.0
            if self.position.direction == "flat"
            else self.instrument.pnl(
                self.position.avg_price,
                mark_price,
                abs(self.position.size),
                self.position.direction,
            )
        )
        for trade in self.open_trades:
            exit_commission = calculate_commission(
                mark_price,
                trade.qty,
                self.config.commission_type,
                self.config.commission_value,
            )
            trade.profit = (
                self.instrument.pnl(
                    trade.entry_price, mark_price, trade.qty, trade.direction
                )
                - trade.commission_entry
                - exit_commission
            )
        self.equity = self.cash + self.position.open_profit

    def _update_intrabar_drawdown(self, bar: Bar) -> None:
        if self.position.direction == "flat" or self.position.avg_price is None:
            return
        adverse_price = bar.low if self.position.direction == "long" else bar.high
        favorable_price = bar.high if self.position.direction == "long" else bar.low
        adverse_profit = self.instrument.pnl(
            self.position.avg_price,
            adverse_price,
            abs(self.position.size),
            self.position.direction,
        )
        favorable_profit = self.instrument.pnl(
            self.position.avg_price,
            favorable_price,
            abs(self.position.size),
            self.position.direction,
        )
        adverse_equity = self.cash + adverse_profit
        favorable_equity = self.cash + favorable_profit
        move = equity_move_from_baseline(
            baseline=self.config.initial_capital,
            adverse_equity=adverse_equity,
            favorable_equity=favorable_equity,
        )
        self.max_drawdown = max(self.max_drawdown, move.drawdown)
        self.max_drawdown_percent = max(
            self.max_drawdown_percent, move.drawdown_percent
        )
        self.max_runup = max(self.max_runup, move.runup)
        self.max_runup_percent = max(self.max_runup_percent, move.runup_percent)
