from __future__ import annotations
import time
from dataclasses import replace
from inspect import signature
from typing import Any
from backtest_engine.config import BacktestConfig
from backtest_engine.context import StrategyContext, StrategyStateView
from backtest_engine.errors import (
    BacktestEngineError,
    BarMagnifierUnavailableError,
    BarValidationError,
    ConfigError,
    ProviderError,
    ResumeUnsupportedError,
    StrategyRuntimeError,
)
from backtest_engine.models import (
    Bar,
    BarSeries,
    Order,
    Fill,
    Position,
    Trade,
    EquityPoint,
    Diagnostic,
    BacktestCallbacks,
    BacktestResumeState,
    InstrumentModel,
)
from backtest_engine.models.timeframe import infer_close_from_timeframe
from backtest_engine.broker.commission import calculate_commission
from backtest_engine.broker.slippage import slippage_value
from backtest_engine.broker.rounding import round_to_step
from backtest_engine.broker.fill_simulator import build_price_path, limit_reached, stop_reached
from backtest_engine.core.deterministic_hash import sha256_obj
from backtest_engine.core.execution_backend_adapter import run_execution_backend
from backtest_engine.core.risk_rules import (
    apply_risk_rules,
    max_position_size_allows,
    pending_entry_position_delta,
)
from backtest_engine.core.strategy_command_processor import flush_strategy_commands
from backtest_engine.core.realtime import (
    BarTickSlice,
    RealtimeTickAttempt,
    RealtimeTickCommitPolicy,
    RuntimeTickUpdate,
    validate_realtime_order_fill_oracle_proof,
)
from backtest_engine.core.score_window import (
    build_score_window_plan,
)
from backtest_engine.core.state_snapshot import (
    BrokerSnapshot,
    RealtimeBrokerSnapshot,
    RealtimeExecutionCheckpoint,
    build_resume_state,
    clone_state,
)
from backtest_engine.core.validation import infer_price_tick, validate_bars
from backtest_engine.core.engine_validation import validate_backtest_config
from backtest_engine.core.result_builder import build_backtest_result
from backtest_engine.ledger.runup_drawdown import trade_excursion_values
from backtest_engine.results import (
    BacktestResult,
    equity_move_from_baseline,
    update_equity_extremes,
)


class _NoopRuntime:
    def begin_bar(self, bar: Bar, bar_index: int) -> None:
        pass

    def end_bar(self) -> None:
        pass


class BacktestEngine:
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
        self.orders: list[Order] = []
        self.fills: list[Fill] = []
        self.closed_trades: list[Trade] = []
        self.open_trades: list[Trade] = []
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
    ) -> BacktestResult:
        t0 = time.perf_counter()
        params = params or {}
        self.callbacks = callbacks or BacktestCallbacks()
        self._callbacks_disabled = False
        self._reset_state()
        self._validate_config()
        series = self._resolve_bars(bars)
        self._effective_mintick = self.config.mintick or infer_price_tick(series)
        series = self._slice_range(series)

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
        if self.config.use_bar_magnifier and (
            not self.config.bar_magnifier_lower_tf or self.config.bar_magnifier_bars is None
        ):
            raise BarMagnifierUnavailableError("bar magnifier lower timeframe bars unavailable")
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
        ctx = StrategyContext(self.config, self.state)
        runtime = self.config.runtime or _NoopRuntime()
        try:
            strategy = strategy_class(params=params, runtime=runtime, ctx=ctx)
        except TypeError:
            strategy = strategy_class(params, runtime)
            strategy.ctx = ctx
        start_index = 0
        if resume_state is not None:
            start_index = self._restore_resume_state(resume_state, strategy, runtime, ctx)
        equity_curve = (
            [] if self._want("equity_curve") or self.config.collect_equity_curve else None
        )
        status = "completed"
        early_reason = None
        for i in range(start_index, len(series)):
            bar = series.get_bar(i)
            self._cb("on_bar_start", bar, i)
            for o in self.orders:
                if o.status == "pending" and o.active_from_bar_index <= i:
                    o.status = "active"
                    self._event("ORDER_ACTIVATED", f"order {o.id} activated", i, bar.time, o.id)
                    self._cb("on_order_activated", o)
            runtime.begin_bar(bar, i)
            self._process_bar_fills(strategy, ctx, bar, i, open_only=True)
            self._update_open_profit(bar.open)
            self._update_state()
            self._call_strategy(strategy, bar, i)
            self._flush(ctx, bar, i)
            self._process_bar_fills(strategy, ctx, bar, i, skip_open=True)
            self._update_intrabar_drawdown(bar)
            self._update_open_profit(bar.close)
            self._update_trade_excursions(bar)
            self._update_state()
            extremes = self._update_equity_extremes(self.equity)
            self._update_state()
            if equity_curve is not None:
                point = EquityPoint(
                    i,
                    bar.time,
                    self.equity,
                    self.cash,
                    self.position.size,
                    self.position.avg_price if self.position.direction != "flat" else None,
                    self.position.open_profit,
                    self.position.realized_profit,
                    extremes.drawdown,
                    extremes.drawdown_percent,
                    extremes.runup,
                    extremes.runup_percent,
                )
                equity_curve.append(point)
                if self._score_mode and i >= self._score_start_index:
                    self._score_equity_points.append(point)
                self._cb("on_equity", point)
            stop_now = False
            if self._early_stop_enabled:
                if self._min_equity_stop is not None and self.equity <= self._min_equity_stop:
                    status = "early_stopped"
                    early_reason = "min_equity_stop"
                    stop_now = True
                if (
                    not stop_now
                    and self._max_drawdown_stop_percent is not None
                    and extremes.drawdown_percent >= self._max_drawdown_stop_percent
                ):
                    status = "early_stopped"
                    early_reason = "max_drawdown_stop_percent"
                    stop_now = True
                if (
                    not stop_now
                    and self._max_drawdown_stop_cash is not None
                    and extremes.drawdown >= self._max_drawdown_stop_cash
                ):
                    status = "early_stopped"
                    early_reason = "max_drawdown_stop_cash"
                    stop_now = True
                if (
                    not stop_now
                    and self._max_bars_without_trade is not None
                    and self.last_trade_bar is not None
                    and i - self.last_trade_bar >= self._max_bars_without_trade
                ):
                    status = "early_stopped"
                    early_reason = "max_bars_without_trade"
                    stop_now = True
            runtime.end_bar()
            self._cb("on_bar_end", bar, i, self.state)
            if stop_now:
                break
        finalize = getattr(strategy, "_finalize", None)
        if callable(finalize):
            finalize()
        if self.config.force_close_on_end and self.position.direction != "flat" and len(series):
            self._force_close(series.get_bar(len(series) - 1), len(series) - 1)
        result = self._result(
            series,
            equity_curve,
            status,
            early_reason,
            (time.perf_counter() - t0) * 1000,
            strategy,
            runtime,
        )
        return result

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

    def _validate_config(self) -> None:
        validate_backtest_config(self.config)

    def _slice_range(self, series: BarSeries) -> BarSeries:
        start = self.config.start_time
        end = self.config.end_time
        idx = [i for i, t in enumerate(series.time) if int(t) >= start and int(t) <= end]
        if not idx:
            return BarSeries([], [], [], [], [], [])
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
        )

    def _resolve_bars(self, bars: BarSeries | list[Bar] | None) -> BarSeries:
        if bars is None:
            raise ProviderError("No bars supplied; load market data outside BacktestEngine")
        if isinstance(bars, BarSeries):
            return bars
        return BarSeries.from_bars(bars)

    def _call_strategy(self, strategy: Any, bar: Bar, i: int) -> None:
        try:
            strategy._process_bar(bar, i)
        except BacktestEngineError:
            raise
        except Exception as e:
            raise StrategyRuntimeError(str(e)) from e

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

    def _qty_from_args(self, kw: dict, current_size: float | None, price: float) -> float:
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
        if self.position.direction == direction and self.config.pyramiding <= 0:
            return False
        existing_orders = sum(
            1
            for o in self.orders
            if o.kind == "entry" and o.direction == direction and o.status in ("pending", "active")
        )
        if existing_orders and self.config.pyramiding <= 0:
            return False
        active = sum(1 for t in self.open_trades if t.direction == direction)
        return active + existing_orders <= self.config.pyramiding

    def _pending_entry_position_delta(self, exclude_order: Order | None = None) -> float:
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
            self._diag("ORDER_REJECTED_ZERO_QTY", "order qty is zero", "warning", i, bar.time, o.id)
            return
        if not self._risk_allows_order(o, bar, i):
            return
        if o.active_from_bar_index <= i:
            o.status = "active"
        self.orders.append(o)
        self._event("ORDER_CREATED", f"order {o.id} created", i, bar.time, o.id)
        self._cb("on_order_created", o)

    def _matching_open_trades(self, from_entry: str | None) -> list[Trade]:
        return [t for t in self.open_trades if from_entry is None or t.entry_id == from_entry]

    def _reserved_qty_by_entry(self, exclude_order: Order | None = None) -> dict[str, float]:
        groups: dict[str, tuple[str | None, float]] = {}
        exclude_key = (
            (exclude_order.parent_exit_id or exclude_order.id)
            if exclude_order is not None
            else None
        )
        for o in self.orders:
            if o is exclude_order or (
                exclude_key is not None and (o.parent_exit_id or o.id) == exclude_key
            ):
                continue
            if o.kind == "exit" and o.status in ("pending", "active"):
                key = o.parent_exit_id or o.id
                qty = o.reserved_qty or o.qty
                prev = groups.get(key)
                if prev is None or qty > prev[1]:
                    groups[key] = (o.from_entry, qty)
        reserved: dict[str, float] = {}
        unreserved = {id(t): t.qty for t in self.open_trades}
        by_entry = {t.entry_id: t for t in self.open_trades}
        for entry, qty in groups.values():
            if entry is not None:
                tr = by_entry.get(entry)
                if tr is None:
                    continue
                q = min(qty, unreserved.get(id(tr), 0.0))
                reserved[entry] = reserved.get(entry, 0.0) + q
                unreserved[id(tr)] = max(0.0, unreserved.get(id(tr), 0.0) - q)
        for entry, qty in groups.values():
            if entry is not None:
                continue
            remaining = qty
            for tr in self.open_trades:
                if remaining <= 0:
                    break
                q = min(remaining, unreserved.get(id(tr), 0.0))
                if q <= 0:
                    continue
                reserved[tr.entry_id] = reserved.get(tr.entry_id, 0.0) + q
                unreserved[id(tr)] -= q
                remaining -= q
        return reserved

    def _reserved_exit_qty(
        self, from_entry: str | None, exclude_order: Order | None = None
    ) -> float:
        by_entry = self._reserved_qty_by_entry(exclude_order)
        if from_entry is None:
            return sum(by_entry.values())
        return by_entry.get(from_entry, 0.0)

    def _available_exit_qty(
        self, from_entry: str | None, exclude_order: Order | None = None
    ) -> float:
        return max(
            0.0,
            sum(t.qty for t in self._matching_open_trades(from_entry))
            - self._reserved_exit_qty(from_entry, exclude_order),
        )

    def _exit_base_price(self, from_entry: str | None) -> float:
        trades = self._matching_open_trades(from_entry)
        qty = sum(t.qty for t in trades)
        return (
            (sum(t.entry_price * t.qty for t in trades) / qty) if qty else self.position.avg_price
        )

    def _update_trailing_order(self, o: Order, price: float) -> None:
        if o.trail_price is None and o.trail_offset is None and o.trail_points is None:
            return
        offset = float(o.trail_offset or 0.0)
        if o.direction == "long":
            if not o.trail_activated and (o.trail_price is None or price >= o.trail_price):
                o.trail_activated = True
            if o.trail_activated:
                o.stop_price = max(
                    o.stop_price if o.stop_price is not None else float("-inf"), price - offset
                )
        else:
            if not o.trail_activated and (o.trail_price is None or price <= o.trail_price):
                o.trail_activated = True
            if o.trail_activated:
                o.stop_price = min(
                    o.stop_price if o.stop_price is not None else float("inf"), price + offset
                )

    def _process_bar_fills(
        self,
        strategy: Any,
        ctx: StrategyContext,
        bar: Bar,
        i: int,
        open_only: bool = False,
        skip_open: bool = False,
    ) -> None:
        recalc = 0
        path = self._price_path(bar)
        path_cursor = 0
        while True:
            filled = False
            restart_after_recalc = False
            for path_index, (price, point) in enumerate(path[path_cursor:], start=path_cursor):
                path_is_open = point == "open" or point.endswith(".open")
                if open_only and not path_is_open:
                    continue
                if skip_open and path_is_open:
                    continue
                for o in list(self.orders):
                    current_bar_close_activation = (
                        self.config.process_orders_on_close and o.created_bar_index == i
                    )
                    if current_bar_close_activation and not (
                        point == "close" or point.endswith(".close")
                    ):
                        continue
                    if o.status != "active":
                        if not (
                            o.status == "pending"
                            and o.created_bar_index == i
                            and o.trail_price is not None
                        ):
                            continue
                        self._update_trailing_order(o, price)
                        continue
                    was_trail_activated = o.trail_activated
                    self._update_trailing_order(o, price)
                    if (
                        path_is_open
                        and o.trail_price is not None
                        and not was_trail_activated
                        and o.trail_activated
                    ):
                        o.stop_price = price
                    if (
                        o.kind == "exit"
                        and o.from_entry is not None
                        and not self._matching_open_trades(o.from_entry)
                    ):
                        continue
                    if o.order_type == "stop" and o.stop_price is None:
                        continue
                    is_open_point = path_is_open
                    is_close_point = point == "close" or point.endswith(".close")
                    fill_price = price
                    if o.order_type == "market" and (
                        (is_open_point and o.created_bar_index < i)
                        or (
                            self.config.calc_on_order_fills
                            and o.created_bar_index == i
                            and o.active_from_bar_index <= i
                        )
                        or (
                            is_close_point
                            and (self.config.process_orders_on_close or o.immediately)
                        )
                    ):
                        pass
                    elif o.order_type == "limit" and limit_reached(
                        o,
                        price,
                        bar,
                        self.config.mintick,
                        self.config.backtest_fill_limits_assumption_ticks,
                    ):
                        fill_price = self._limit_fill_price(o, price, is_open_point)
                    elif o.order_type == "stop" and stop_reached(o, price):
                        if self.config.stop_gap_fill_policy == "stop_price":
                            fill_price = o.stop_price or price
                        elif not is_open_point and not self.config.fill_worse_stop_at_path_price:
                            fill_price = (
                                o.stop_price
                                if (
                                    o.stop_price is not None
                                    and not (
                                        self.config.calc_on_order_fills
                                        and o.created_bar_index == i
                                        and o.active_from_bar_index <= i
                                    )
                                )
                                else price
                            )
                    elif o.order_type == "stop_limit":
                        if not o.stop_limit_activated and stop_reached(o, price):
                            o.stop_limit_activated = True
                            self._event(
                                "STOP_LIMIT_ACTIVATED",
                                f"stop-limit {o.id} activated",
                                i,
                                bar.time,
                                o.id,
                            )
                        if not (
                            o.stop_limit_activated
                            and limit_reached(
                                o,
                                price,
                                bar,
                                self.config.mintick,
                                self.config.backtest_fill_limits_assumption_ticks,
                            )
                        ):
                            continue
                        fill_price = self._limit_fill_price(o, price, is_open_point)
                    else:
                        continue
                    self._fill(o, bar, i, fill_price, point)
                    filled = True
                    if self.config.calc_on_order_fills and not current_bar_close_activation:
                        self._update_open_profit(fill_price)
                        self._update_state()
                        recalc += 1
                        if recalc > self.config.max_recalc_depth:
                            self._diag(
                                "MAX_RECALC_DEPTH_REACHED",
                                "max recalc depth reached",
                                "warning",
                                i,
                                bar.time,
                            )
                            return
                        self._call_strategy(strategy, bar, i)
                        self._flush(ctx, bar, i, recalc_after_fill=True)
                        path_cursor = path_index
                        restart_after_recalc = True
                        break
                if restart_after_recalc:
                    break
                if self._maybe_margin_call(price, bar, i, point):
                    filled = True
                    if self.config.calc_on_order_fills:
                        self._update_open_profit(price)
                        self._update_state()
                        recalc += 1
                        if recalc > self.config.max_recalc_depth:
                            self._diag(
                                "MAX_RECALC_DEPTH_REACHED",
                                "max recalc depth reached",
                                "warning",
                                i,
                                bar.time,
                            )
                            return
                        self._call_strategy(strategy, bar, i)
                        self._flush(ctx, bar, i, recalc_after_fill=True)
                        path_cursor = path_index
                        restart_after_recalc = True
                        break
            if restart_after_recalc and path_cursor < len(path):
                continue
            if not (self.config.calc_on_order_fills and filled):
                break
            break

    def _maybe_margin_call(self, price: float, bar: Bar, i: int, point: str) -> bool:
        if self.position.direction == "flat" or self.position.avg_price is None:
            return False
        margin_percent = (
            self.config.margin_long
            if self.position.direction == "long"
            else self.config.margin_short
        )
        if margin_percent >= 100.0:
            return False
        margin_ratio = margin_percent / 100.0
        qty_abs = abs(self.position.size)
        if qty_abs <= 0.0 or price <= 0.0 or margin_ratio <= 0.0:
            return False
        self._update_open_profit(price)
        margin_required = price * qty_abs * margin_ratio
        available_funds = self.equity - margin_required
        if available_funds > 1e-12:
            return False
        cover_raw = (-available_funds / margin_ratio) / price
        liquidation_qty = 1.0 if cover_raw < 1.0 else float(int(cover_raw) * 4)
        if self.config.qty_step:
            liquidation_qty = round_to_step(liquidation_qty, self.config.qty_step, "floor")
        liquidation_qty = min(qty_abs, liquidation_qty)
        if liquidation_qty <= 0.0:
            return False
        order = Order(
            "Margin call",
            "close",
            self.position.direction,
            "sell" if self.position.direction == "long" else "buy",
            "close",
            "market",
            liquidation_qty,
            i,
            bar.time,
            i,
            self.position.direction,
            True,
            immediately=True,
        )
        self._fill(order, bar, i, price, point)
        self._event(
            "MARGIN_CALL", f"margin call liquidated {liquidation_qty}", i, bar.time, order.id
        )
        return True

    def _limit_fill_price(self, o: Order, path_price: float, is_open_point: bool) -> float:
        limit = o.limit_price if o.limit_price is not None else path_price
        if is_open_point and self.config.limit_gap_fill_policy in ("tradingview", "open_price"):
            if o.side == "sell" and path_price >= limit:
                return path_price
            if o.side == "buy" and path_price <= limit:
                return path_price
        return limit

    def _price_path(self, bar: Bar) -> list[tuple[float, str]]:
        if self.config.fill_model == "close_only":
            return [(bar.close, "close")]
        if not self.config.use_bar_magnifier:
            return build_price_path(bar)
        if not self.config.bar_magnifier_lower_tf or self.config.bar_magnifier_bars is None:
            return build_price_path(bar)
        try:
            lower = self.config.bar_magnifier_bars.get(bar.time, ())
            lower_series = lower if isinstance(lower, BarSeries) else BarSeries.from_bars(lower)
            self._validate_lower_timeframe_bars(lower_series, bar)
        except Exception as e:
            raise BarMagnifierUnavailableError(str(e)) from e
        if len(lower_series) == 0:
            raise BarMagnifierUnavailableError("empty lower timeframe bars")
        path: list[tuple[float, str]] = []
        for j in range(len(lower_series)):
            lb = lower_series.get_bar(j)
            for price, point in build_price_path(lb):
                path.append((price, f"lower[{j}].{point}"))
        return path

    def _validate_lower_timeframe_bars(self, lower_series: BarSeries, parent: Bar) -> None:
        """Fail closed on malformed bar-magnifier data before using intrabars."""
        parent_close = parent.time_close
        if parent_close is None:
            parent_close = self._infer_parent_close(parent.time)
        last_time: int | None = None
        seen: set[int] = set()
        for j in range(len(lower_series)):
            lb = lower_series.get_bar(j)
            if last_time is not None and lb.time < last_time:
                raise BarValidationError("lower timeframe bars are not sorted")
            if lb.time in seen:
                raise BarValidationError(f"duplicate lower timeframe bar time {lb.time}")
            seen.add(lb.time)
            last_time = lb.time
            if lb.time < parent.time or lb.time >= parent_close:
                raise BarValidationError("lower timeframe bar outside parent window")
            if lb.time_close is None:
                raise BarValidationError("lower timeframe bar missing time_close")
            if lb.time_close <= lb.time:
                raise BarValidationError("lower timeframe bar has invalid/open time_close")
            if lb.time_close > parent_close:
                raise BarValidationError("lower timeframe bar closes outside parent window")
            if lb.high < max(lb.open, lb.close, lb.low):
                raise BarValidationError("lower timeframe bar has invalid OHLC high")
            if lb.low > min(lb.open, lb.close, lb.high):
                raise BarValidationError("lower timeframe bar has invalid OHLC low")

    def _validate_supplied_bar_magnifier_bars(self, series: BarSeries) -> None:
        for i in range(len(series)):
            parent = series.get_bar(i)
            lower = self.config.bar_magnifier_bars.get(parent.time, ())
            if not lower:
                continue
            lower_series = lower if isinstance(lower, BarSeries) else BarSeries.from_bars(lower)
            try:
                self._validate_lower_timeframe_bars(lower_series, parent)
            except Exception as exc:
                raise BarMagnifierUnavailableError(str(exc)) from exc

    def _infer_parent_close(self, parent_open: int) -> int:
        return infer_close_from_timeframe(parent_open, self.config.timeframe)

    def _fill(self, o: Order, bar: Bar, i: int, price: float, point: str) -> None:
        if o.kind == "exit":
            avail = self._available_exit_qty(o.from_entry, exclude_order=o)
            if avail <= 0:
                code = (
                    "ORDER_REJECTED_NO_MATCHING_ENTRY"
                    if o.from_entry is not None
                    else "ORDER_REJECTED_NO_AVAILABLE_POSITION_QTY"
                )
                self._diag(
                    code,
                    "reduce order has no matching unreserved qty",
                    "warning",
                    i,
                    bar.time,
                    o.id,
                )
                return
            o.qty = min(o.qty, avail)
        # TradingView applies `slippage` to market/stop-style fills, not to passive
        # limit fills. Applying tick slippage to limit orders makes buy limits fill
        # worse than their own limit price and breaks the Stage 2B TV oracle.
        if o.order_type == "stop" and self.config.mintick:
            # A stop market order becomes marketable only once the stop level is
            # reached on the instrument tick grid. TradingView rounds buy stops
            # up and sell stops down before applying slippage.
            price = round_to_step(
                price, self.config.mintick, "ceil" if o.side == "buy" else "floor"
            )
        slip_raw = 0.0 if o.order_type in {"limit", "stop_limit"} else self.config.slippage
        slip = slippage_value(
            price,
            o.side,
            o.position_effect,
            slip_raw,
            self.config.slippage_type,
            self.config.mintick,
        )
        rounding_mode = self.config.price_rounding
        if o.order_type in {"limit", "stop_limit"} and self.config.mintick:
            # Passive limit fills must remain executable on the tick grid without
            # crossing the order side. For buy limits, do not round up above the
            # fill/limit price; for sell limits, do not round down below it.
            # Buy → round DOWN (floor) to stay at or below limit.
            # Sell → round DOWN (floor) to stay at or below limit, which is the
            # conservative side for fills (matches TV equity fill semantics).
            rounding_mode = "floor"
        fprice = round_to_step(price + slip, self.config.mintick, rounding_mode)
        before = self.position.direction
        com = calculate_commission(
            fprice, o.qty, self.config.commission_type, self.config.commission_value
        )
        self.cash -= com
        self.position.realized_profit -= com
        after = self._apply_position(o, fprice, bar, i, com)
        fill = Fill(
            o.id,
            i,
            bar.time,
            fprice,
            o.qty,
            o.direction,
            o.side,
            o.position_effect,
            before,
            after,
            "filled",
            com,
            slip,
            point,
        )
        self.fills.append(fill)
        o.status = "filled"
        self.last_trade_bar = i
        self._cb("on_fill", fill)
        self._event("ORDER_FILLED", f"order {o.id} filled", i, bar.time, o.id)
        self._apply_oca(o, bar, i)

    def _apply_position(self, o: Order, price: float, bar: Bar, i: int, commission: float) -> str:
        signed = o.qty if o.side == "buy" else -o.qty
        if self.position.direction == "flat" or (self.position.size == 0):
            self.position.size = signed
            self.position.direction = "long" if signed > 0 else "short"
            self.position.avg_price = price
            tr = Trade(
                o.id,
                o.id,
                None,
                self.position.direction,
                bar.time,
                i,
                price,
                None,
                None,
                None,
                abs(signed),
                commission,
                0.0,
                -commission,
                0.0,
                is_open=True,
            )
            self.open_trades.append(tr)
            self._cb("on_trade_open", tr)
            return self.position.direction
        cur_sign = 1 if self.position.direction == "long" else -1
        if signed * cur_sign > 0:
            newabs = abs(self.position.size) + abs(signed)
            self.position.avg_price = (
                self.position.avg_price * abs(self.position.size) + price * abs(signed)
            ) / newabs
            self.position.size += signed
            tr = Trade(
                o.id,
                o.id,
                None,
                self.position.direction,
                bar.time,
                i,
                price,
                None,
                None,
                None,
                abs(signed),
                commission,
                0.0,
                -commission,
                0.0,
                is_open=True,
            )
            self.open_trades.append(tr)
            self._cb("on_trade_open", tr)
            return self.position.direction
        qty_close = min(abs(signed), abs(self.position.size))
        targets = [
            t for t in self.open_trades if o.from_entry is None or t.entry_id == o.from_entry
        ]
        if not targets:
            self._diag(
                "ORDER_REJECTED_NO_MATCHING_ENTRY",
                "reduce order has no matching from_entry",
                "warning",
                i,
                bar.time,
                o.id,
            )
            return self.position.direction
        reserved = self._reserved_qty_by_entry(exclude_order=o)
        target_caps = {
            id(t): max(
                0.0, t.qty - (reserved.get(t.entry_id, 0.0) if o.from_entry is None else 0.0)
            )
            for t in targets
        }
        targets = [t for t in targets if target_caps[id(t)] > 0]
        if not targets:
            self._diag(
                "ORDER_REJECTED_NO_AVAILABLE_POSITION_QTY",
                "reduce order has no matching unreserved qty",
                "warning",
                i,
                bar.time,
                o.id,
            )
            return self.position.direction
        qty_close = min(qty_close, sum(target_caps[id(t)] for t in targets))
        gross = 0.0
        rem_for_profit = qty_close
        for trp in targets:
            if rem_for_profit <= 0:
                break
            q = min(target_caps[id(trp)], rem_for_profit)
            gross += self.instrument.pnl(trp.entry_price, price, q, trp.direction)
            rem_for_profit -= q
        # _fill debits commission exactly once for the whole order.  Closing a
        # position therefore adds only gross PnL to cash/realized PnL here,
        # while the closed-trade ledger remains net of prorated entry + exit
        # commission.  For reversals, only the close portion of the order's
        # commission belongs to the closed trade; the remainder becomes the
        # entry commission of the newly opened opposite lot.
        exit_commission_total = commission * (qty_close / o.qty) if o.qty else commission
        opening_commission = max(0.0, commission - exit_commission_total)
        self.cash += gross
        self.position.realized_profit += gross
        remaining = qty_close
        for tr in list(targets):
            if remaining <= 0:
                break
            q = min(target_caps[id(tr)], remaining)
            exit_commission = exit_commission_total * (q / qty_close) if qty_close else 0.0
            entry_commission = tr.commission_entry * (q / tr.qty) if tr.qty else 0.0
            p = (
                self.instrument.pnl(tr.entry_price, price, q, tr.direction)
                - exit_commission
                - entry_commission
            )
            mfe, mae, max_runup, max_drawdown = self._trade_excursion_values(tr, bar)
            closed = replace(
                tr,
                exit_id=o.id,
                exit_time=bar.time,
                exit_bar_index=i,
                exit_price=price,
                qty=q,
                commission_entry=entry_commission,
                commission_exit=exit_commission,
                profit=p,
                profit_percent=(p / (tr.entry_price * q) * 100 if tr.entry_price * q else 0.0),
                mfe=mfe,
                mae=mae,
                max_runup=max_runup,
                max_drawdown=max_drawdown,
                exit_reason=o.id,
                bars_held=i - tr.entry_bar_index,
                is_open=False,
            )
            self.closed_trades.append(closed)
            self._cb("on_trade_close", closed)
            tr.qty -= q
            tr.commission_entry -= entry_commission
            remaining -= q
            if tr.qty <= 1e-12:
                self.open_trades.remove(tr)
        self.position.size += signed
        if abs(self.position.size) < 1e-12:
            self.position = Position(realized_profit=self.position.realized_profit)
            return "flat"
        same_dir = [t for t in self.open_trades if t.direction == self.position.direction]
        if same_dir:
            q = sum(t.qty for t in same_dir)
            self.position.avg_price = sum(t.entry_price * t.qty for t in same_dir) / q
        if self.position.size * cur_sign < 0:
            self.position.direction = "long" if self.position.size > 0 else "short"
            self.position.avg_price = price
            tr = Trade(
                o.id,
                o.id,
                None,
                self.position.direction,
                bar.time,
                i,
                price,
                None,
                None,
                None,
                abs(self.position.size),
                opening_commission,
                0.0,
                -opening_commission,
                0.0,
                is_open=True,
            )
            self.open_trades.append(tr)
            self._cb("on_trade_open", tr)
        return self.position.direction

    def _update_trade_excursions(self, bar: Bar) -> None:
        if not self.config.collect_mfe_mae:
            return
        for tr in self.open_trades:
            tr.mfe, tr.mae, tr.max_runup, tr.max_drawdown = self._trade_excursion_values(tr, bar)
            tr.profit = (
                self.instrument.pnl(tr.entry_price, bar.close, tr.qty, tr.direction)
                - tr.commission_entry
            )
            self._cb("on_trade_update", tr)

    def _trade_excursion_values(self, tr: Trade, bar: Bar) -> tuple[float, float, float, float]:
        return trade_excursion_values(tr, bar, self.instrument)

    def _apply_oca(self, o: Order, bar: Bar, i: int) -> None:
        if not o.oca_name:
            return
        for other in self.orders:
            if (
                other is not o
                and other.status in ("pending", "active")
                and other.oca_name == o.oca_name
            ):
                if o.oca_type == "cancel":
                    other.status = "cancelled"
                    self._cb("on_order_cancelled", other)
                    self._event(
                        "ORDER_CANCELLED", f"OCA cancelled order {other.id}", i, bar.time, other.id
                    )
                elif o.oca_type == "reduce":
                    other.qty = max(0.0, other.qty - o.qty)
                    if other.qty <= 0:
                        other.status = "cancelled"
                        self._cb("on_order_cancelled", other)
                        self._event(
                            "ORDER_CANCELLED",
                            f"OCA reduced order {other.id} to zero",
                            i,
                            bar.time,
                            other.id,
                        )
                    else:
                        self._event(
                            "ORDER_MODIFIED", f"OCA reduced order {other.id}", i, bar.time, other.id
                        )

    def _force_close(self, bar: Bar, i: int) -> None:
        o = Order(
            "forced_end_close",
            "close",
            self.position.direction,
            "sell" if self.position.direction == "long" else "buy",
            "close",
            "market",
            abs(self.position.size),
            i,
            bar.time,
            i,
            self.position.direction,
            True,
            immediately=True,
        )
        self._fill(o, bar, i, bar.close, "close")

    def _update_open_profit(self, price: float) -> None:
        self.position.open_profit = (
            0.0
            if self.position.direction == "flat"
            else self.instrument.pnl(
                self.position.avg_price, price, abs(self.position.size), self.position.direction
            )
        )
        self.equity = self.cash + self.position.open_profit

    def _update_intrabar_drawdown(self, bar: Bar) -> None:
        if self.position.direction == "flat" or self.position.avg_price is None:
            return
        adverse_price = bar.low if self.position.direction == "long" else bar.high
        favorable_price = bar.high if self.position.direction == "long" else bar.low
        adverse_profit = self.instrument.pnl(
            self.position.avg_price, adverse_price, abs(self.position.size), self.position.direction
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
        self.max_drawdown_percent = max(self.max_drawdown_percent, move.drawdown_percent)
        self.max_runup = max(self.max_runup, move.runup)
        self.max_runup_percent = max(self.max_runup_percent, move.runup_percent)

    def _update_equity_extremes(self, equity: float):
        extremes = update_equity_extremes(
            equity=equity,
            peak_equity=self.peak_equity,
            trough_equity=self.trough_equity,
            max_drawdown=self.max_drawdown,
            max_drawdown_percent=self.max_drawdown_percent,
            max_runup=self.max_runup,
            max_runup_percent=self.max_runup_percent,
        )
        self.peak_equity = extremes.peak_equity
        self.trough_equity = extremes.trough_equity
        self.max_drawdown = extremes.max_drawdown
        self.max_drawdown_percent = extremes.max_drawdown_percent
        self.max_runup = extremes.max_runup
        self.max_runup_percent = extremes.max_runup_percent
        return extremes

    def _update_state(self) -> None:
        self.state.position_size = self.position.size
        self.state.position_avg_price = (
            None if self.position.direction == "flat" else self.position.avg_price
        )
        self.state.position_direction = self.position.direction
        self.state.cash = self.cash
        self.state.equity = self.equity
        self.state.open_profit = self.position.open_profit
        self.state.net_profit = self.equity - self.config.initial_capital
        self.state.gross_profit = sum(t.profit for t in self.closed_trades if t.profit > 0)
        self.state.gross_loss = abs(sum(t.profit for t in self.closed_trades if t.profit < 0))
        self.state.max_drawdown = self.max_drawdown
        self.state.max_drawdown_percent = self.max_drawdown_percent
        self.state.max_runup = self.max_runup
        self.state.max_runup_percent = self.max_runup_percent
        self.state.closed_trades = len(self.closed_trades)
        self.state.open_trades = len(self.open_trades)

    def _want(self, name: str) -> bool:
        return name in self.config.required_outputs

    def _event(self, code, msg, i=None, t=None, oid=None) -> None:
        if self.config.collect_events:
            self.events.append(Diagnostic(code, msg, "info", i, t, oid))

    def _diag(self, code, msg, severity, i=None, t=None, oid=None) -> None:
        d = Diagnostic(code, msg, severity, i, t, oid)
        (self.errors if severity == "error" else self.warnings).append(d)
        self._cb("on_diagnostic", d)

    def _cb(self, name: str, *args: Any) -> None:
        fn = getattr(self.callbacks, name, None)
        if fn and not self._callbacks_disabled:
            try:
                fn(*args)
            except Exception as e:
                if self.config.callback_error_policy == "raise":
                    raise
                self.warnings.append(Diagnostic("CALLBACK_ERROR", str(e), "warning"))
                if self.config.callback_error_policy == "disable_callbacks":
                    self._callbacks_disabled = True

    def _config_hash(self) -> str:
        snapshot = self.config.snapshot()
        snapshot.pop("export_resume_state", None)
        return sha256_obj(snapshot)

    def _export_realtime_broker_state(self) -> RealtimeBrokerSnapshot:
        """Export a detached broker checkpoint for future realtime tick rollback."""

        return RealtimeBrokerSnapshot(
            cash=self.cash,
            equity=self.equity,
            peak_equity=self.peak_equity,
            max_drawdown=self.max_drawdown,
            max_drawdown_percent=self.max_drawdown_percent,
            trough_equity=self.trough_equity,
            max_runup=self.max_runup,
            max_runup_percent=self.max_runup_percent,
            position=clone_state(self.position),
            orders=clone_state(self.orders),
            fills=clone_state(self.fills),
            closed_trades=clone_state(self.closed_trades),
            open_trades=clone_state(self.open_trades),
            last_trade_bar=self.last_trade_bar,
            events=clone_state(self.events),
            warnings=clone_state(self.warnings),
            errors=clone_state(self.errors),
        )

    def _restore_realtime_broker_state(
        self, snapshot: RealtimeBrokerSnapshot, ctx: StrategyContext | None = None
    ) -> None:
        """Restore a detached broker checkpoint and refresh StrategyStateView refs."""

        if not isinstance(snapshot, RealtimeBrokerSnapshot):
            raise ResumeUnsupportedError(
                "realtime broker rollback requires a RealtimeBrokerSnapshot"
            )
        self.cash = snapshot.cash
        self.equity = snapshot.equity
        self.peak_equity = snapshot.peak_equity
        self.max_drawdown = snapshot.max_drawdown
        self.max_drawdown_percent = snapshot.max_drawdown_percent
        self.trough_equity = snapshot.trough_equity
        self.max_runup = snapshot.max_runup
        self.max_runup_percent = snapshot.max_runup_percent
        self.position = clone_state(snapshot.position)
        self.orders = clone_state(snapshot.orders)
        self.fills = clone_state(snapshot.fills)
        self.closed_trades = clone_state(snapshot.closed_trades)
        self.open_trades = clone_state(snapshot.open_trades)
        self.last_trade_bar = snapshot.last_trade_bar
        self.events = clone_state(snapshot.events)
        self.warnings = clone_state(snapshot.warnings)
        self.errors = clone_state(snapshot.errors)
        self.state = StrategyStateView(
            initial_capital=self.config.initial_capital,
            cash=self.cash,
            equity=self.equity,
            _open_trades_ref=self.open_trades,
            _closed_trades_ref=self.closed_trades,
        )
        if ctx is not None:
            ctx.state = self.state
        self._update_state()

    def _export_realtime_execution_checkpoint(
        self, *, strategy: Any | None = None, runtime: Any | None = None
    ) -> RealtimeExecutionCheckpoint:
        """Export combined broker/runtime/strategy checkpoint for tick rollback."""

        runtime_export = getattr(runtime, "export_state", None) if runtime is not None else None
        strategy_export = getattr(strategy, "export_state", None) if strategy is not None else None
        runtime_state = None
        if callable(runtime_export):
            try:
                params = signature(runtime_export).parameters
                runtime_state = (
                    runtime_export(include_varip=False)
                    if "include_varip" in params
                    else runtime_export()
                )
            except (TypeError, ValueError):
                runtime_state = runtime_export()
        return RealtimeExecutionCheckpoint(
            broker_state=self._export_realtime_broker_state(),
            runtime_state=clone_state(runtime_state),
            strategy_state=clone_state(strategy_export()) if callable(strategy_export) else None,
        )

    def _restore_realtime_execution_checkpoint(
        self,
        checkpoint: RealtimeExecutionCheckpoint,
        *,
        ctx: StrategyContext | None = None,
        strategy: Any | None = None,
        runtime: Any | None = None,
    ) -> None:
        """Restore combined broker/runtime/strategy checkpoint for tick rollback."""

        if not isinstance(checkpoint, RealtimeExecutionCheckpoint):
            raise ResumeUnsupportedError(
                "realtime execution rollback requires a RealtimeExecutionCheckpoint"
            )
        self._restore_realtime_broker_state(checkpoint.broker_state, ctx)
        if checkpoint.runtime_state is not None:
            restore = getattr(runtime, "restore_state", None) if runtime is not None else None
            if not callable(restore):
                raise ResumeUnsupportedError(
                    "runtime_state is present but runtime does not implement restore_state(state)"
                )
            restore(clone_state(checkpoint.runtime_state))
        if checkpoint.strategy_state is not None:
            restore = getattr(strategy, "restore_state", None) if strategy is not None else None
            if not callable(restore):
                raise ResumeUnsupportedError(
                    "strategy_state is present but strategy does not implement restore_state(state)"
                )
            restore(clone_state(checkpoint.strategy_state))

    def _guarded_realtime_tick_loop_skeleton(
        self,
        tick_slice: BarTickSlice,
        *,
        ctx: StrategyContext,
        strategy: Any | None = None,
        runtime: Any | None = None,
        on_attempt: Any | None = None,
    ) -> tuple[RealtimeTickAttempt, ...]:
        """Create rollback-guarded tick attempts without enabling tick execution.

        Each tick gets a combined execution checkpoint, optional local mutation
        hook, and immediate restore. The method is intentionally not called from
        ``run()`` while ``calc_on_every_tick`` remains fail-closed.
        """

        attempts: list[RealtimeTickAttempt] = []
        for tick_index, tick in enumerate(tick_slice.ticks):
            checkpoint = self._export_realtime_execution_checkpoint(
                strategy=strategy, runtime=runtime
            )
            if callable(on_attempt):
                on_attempt(tick, tick_index)
            self._restore_realtime_execution_checkpoint(
                checkpoint, ctx=ctx, strategy=strategy, runtime=runtime
            )
            attempts.append(
                RealtimeTickAttempt(
                    bar_index=tick_slice.bar_index,
                    tick_index=tick_index,
                    tick=tick,
                    checkpoint=checkpoint,
                    rolled_back=True,
                )
            )
        return tuple(attempts)

    def _guarded_realtime_strategy_tick_loop_skeleton(
        self,
        tick_slice: BarTickSlice,
        *,
        ctx: StrategyContext,
        strategy: Any,
        runtime: Any | None = None,
        commit_policy: RealtimeTickCommitPolicy | None = None,
    ) -> tuple[RealtimeTickAttempt, ...]:
        """Invoke strategy once per tick under rollback, without committing effects.

        This is a guarded scaffold for future ``calc_on_every_tick`` work. It is
        intentionally not wired into ``run()`` and restores broker, runtime,
        strategy, and command-buffer state after every attempted tick.
        """

        policy = commit_policy or RealtimeTickCommitPolicy()
        if policy.allow_intrabar_order_fills:
            validate_realtime_order_fill_oracle_proof(policy.intrabar_order_fill_oracle_proof)
        attempts: list[RealtimeTickAttempt] = []
        update_realtime_tick = (
            getattr(runtime, "update_realtime_tick", None) if runtime is not None else None
        )
        total_ticks = len(tick_slice.ticks)
        for tick_index, tick in enumerate(tick_slice.ticks):
            action = policy.action_for(tick_index, total_ticks)
            checkpoint = self._export_realtime_execution_checkpoint(
                strategy=strategy, runtime=runtime
            )
            buffered_commands = clone_state(ctx.buffer.commands)
            committed = False
            try:
                current_bar = tick_slice.bar
                if callable(update_realtime_tick):
                    maybe_bar = update_realtime_tick(
                        RuntimeTickUpdate(
                            price=tick.price,
                            volume=float(tick.volume or 0.0),
                            time=tick.time,
                            is_final=tick_index == total_ticks - 1,
                        )
                    )
                    if maybe_bar is not None:
                        current_bar = maybe_bar
                self._call_strategy(strategy, current_bar, tick_slice.bar_index)
                if action == "commit_final" and len(ctx.buffer.commands) != len(buffered_commands):
                    raise ConfigError(
                        "realtime order commands require TradingView intrabar order/fill oracle evidence"
                    )
                committed = action == "commit_final"
            finally:
                if not committed:
                    self._restore_realtime_execution_checkpoint(
                        checkpoint, ctx=ctx, strategy=strategy, runtime=runtime
                    )
                    ctx.buffer.commands = clone_state(buffered_commands) or []
            attempts.append(
                RealtimeTickAttempt(
                    bar_index=tick_slice.bar_index,
                    tick_index=tick_index,
                    tick=tick,
                    checkpoint=checkpoint,
                    rolled_back=not committed,
                    strategy_invoked=True,
                    policy=action,
                    committed=committed,
                )
            )
        return tuple(attempts)

    def _restore_resume_state(
        self, resume_state: BacktestResumeState, strategy: Any, runtime: Any, ctx: StrategyContext
    ) -> int:
        if resume_state.broker_state is None:
            raise ResumeUnsupportedError(
                "resume_state is missing broker_state; use BacktestEngine export_resume_state or provide a compatible snapshot"
            )
        expected_hash = self._config_hash()
        if resume_state.config_snapshot_hash != expected_hash:
            msg = "resume state config hash does not match current config snapshot"
            if self.config.resume_validation_policy == "strict":
                raise ResumeUnsupportedError(msg)
            self._diag("RESUME_CONFIG_MISMATCH", msg, "warning")
        broker = resume_state.broker_state
        if not isinstance(broker, BrokerSnapshot):
            raise ResumeUnsupportedError(
                "resume_state.broker_state must be a BrokerSnapshot from core.state_snapshot"
            )
        self.cash = broker.cash
        self.equity = broker.equity
        self.peak_equity = broker.peak_equity
        self.max_drawdown = broker.max_drawdown
        self.max_drawdown_percent = broker.max_drawdown_percent
        self.trough_equity = broker.trough_equity
        self.max_runup = broker.max_runup
        self.max_runup_percent = broker.max_runup_percent
        self.position = broker.position
        self.orders = broker.orders
        self.fills = broker.fills
        self.closed_trades = broker.closed_trades
        self.open_trades = broker.open_trades
        self.last_trade_bar = broker.last_trade_bar
        self.state = StrategyStateView(
            initial_capital=self.config.initial_capital,
            cash=self.cash,
            equity=self.equity,
            _open_trades_ref=self.open_trades,
            _closed_trades_ref=self.closed_trades,
        )
        ctx.state = self.state
        self._update_state()
        if resume_state.runtime_state is not None:
            restore = getattr(runtime, "restore_state", None)
            if not callable(restore):
                raise ResumeUnsupportedError(
                    "runtime_state is present but runtime does not implement restore_state(state)"
                )
            restore(resume_state.runtime_state)
        if resume_state.strategy_state is not None:
            restore = getattr(strategy, "restore_state", None)
            if not callable(restore):
                raise ResumeUnsupportedError(
                    "strategy_state is present but strategy does not implement restore_state(state)"
                )
            restore(resume_state.strategy_state)
        return max(0, resume_state.bar_index + 1)

    def _export_resume_state(
        self, bar_index: int, strategy: Any | None = None, runtime: Any | None = None
    ) -> BacktestResumeState:
        strategy_export = getattr(strategy, "export_state", None) if strategy is not None else None
        runtime_export = getattr(runtime, "export_state", None) if runtime is not None else None
        strategy_state = strategy_export() if callable(strategy_export) else None
        runtime_state = runtime_export() if callable(runtime_export) else None
        if strategy is not None and strategy_state is None:
            self._diag(
                "RESUME_STRATEGY_STATE_UNAVAILABLE",
                "strategy does not implement export_state(); resume snapshot contains engine/runtime state only",
                "warning",
            )
        broker = BrokerSnapshot(
            self.cash,
            self.equity,
            self.peak_equity,
            self.max_drawdown,
            self.max_drawdown_percent,
            self.trough_equity,
            self.max_runup,
            self.max_runup_percent,
            self.position,
            self.orders,
            self.fills,
            self.closed_trades,
            self.open_trades,
            self.last_trade_bar,
        )
        return build_resume_state(
            bar_index=bar_index,
            config_snapshot_hash=self._config_hash(),
            broker_state=broker,
            strategy_state=strategy_state,
            runtime_state=runtime_state,
            metadata={"resume_contract": "engine-broker-snapshot-v1"},
        )

    def _result(
        self,
        series: BarSeries,
        equity_curve: list[EquityPoint] | None,
        status: str,
        reason: str | None,
        ms: float,
        strategy: Any | None = None,
        runtime: Any | None = None,
    ) -> BacktestResult:
        return build_backtest_result(
            self,
            series,
            equity_curve,
            status,
            reason,
            ms,
            strategy,
            runtime,
        )
