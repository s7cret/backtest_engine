from __future__ import annotations
from .command_buffer import CommandBuffer
from .strategy_state_view import StrategyStateView
from backtest_engine.errors import UnsupportedRiskRuleError


class StrategyContext:
    def __init__(self, config: object, state: StrategyStateView | None = None):
        self.config = config
        self.state = state or StrategyStateView()
        self.buffer = CommandBuffer()

    def entry(
        self,
        id: str,
        direction: str,
        qty: float | None = None,
        limit: float | None = None,
        stop: float | None = None,
        oca_name: str | None = None,
        oca_type: str | None = None,
        comment: str | None = None,
    ) -> None:
        self.buffer.add(
            "entry",
            id=id,
            direction=direction,
            qty=qty,
            limit=limit,
            stop=stop,
            oca_name=oca_name,
            oca_type=oca_type,
            comment=comment,
        )

    def order(
        self,
        id: str,
        direction: str,
        qty: float | None = None,
        limit: float | None = None,
        stop: float | None = None,
        oca_name: str | None = None,
        oca_type: str | None = None,
        comment: str | None = None,
    ) -> None:
        self.buffer.add(
            "order",
            id=id,
            direction=direction,
            qty=qty,
            limit=limit,
            stop=stop,
            oca_name=oca_name,
            oca_type=oca_type,
            comment=comment,
        )

    def exit(
        self,
        id: str,
        from_entry: str | None = None,
        qty: float | None = None,
        qty_percent: float | None = None,
        limit: float | None = None,
        stop: float | None = None,
        profit: float | None = None,
        loss: float | None = None,
        trail_price: float | None = None,
        trail_points: float | None = None,
        trail_offset: float | None = None,
        oca_name: str | None = None,
        oca_type: str | None = None,
        comment: str | None = None,
    ) -> None:
        self.buffer.add(
            "exit",
            id=id,
            from_entry=from_entry,
            qty=qty,
            qty_percent=qty_percent,
            limit=limit,
            stop=stop,
            profit=profit,
            loss=loss,
            trail_price=trail_price,
            trail_points=trail_points,
            trail_offset=trail_offset,
            oca_name=oca_name,
            oca_type=oca_type,
            comment=comment,
        )

    def close(
        self,
        id: str,
        qty: float | None = None,
        qty_percent: float | None = None,
        immediately: bool = False,
        comment: str | None = None,
    ) -> None:
        self.buffer.add(
            "close",
            id=id,
            qty=qty,
            qty_percent=qty_percent,
            immediately=immediately,
            comment=comment,
        )

    def close_all(self, immediately: bool = False, comment: str | None = None) -> None:
        self.buffer.add("close_all", immediately=immediately, comment=comment)

    def cancel(self, id: str) -> None:
        self.buffer.add("cancel", id=id)

    def cancel_all(self) -> None:
        self.buffer.add("cancel_all")

    def risk_allow_entry_in(self, direction: str) -> None:
        value = str(direction).lower()
        if value in {"long", "strategy.direction.long"}:
            self.config.allow_long = True
            self.config.allow_short = False
            return
        if value in {"short", "strategy.direction.short"}:
            self.config.allow_long = False
            self.config.allow_short = True
            return
        if value in {"all", "both", "strategy.direction.all"}:
            self.config.allow_long = True
            self.config.allow_short = True
            return
        raise ValueError(f"unsupported risk_allow_entry_in direction: {direction!r}")

    def risk_max_drawdown(self, value: float, type: str) -> None:
        self.config.early_stop_enabled = True
        value_type = str(type).lower()
        if value_type in {"percent", "percent_of_equity", "strategy.percent_of_equity"}:
            self.config.max_drawdown_stop_percent = float(value)
            return
        if value_type in {"cash", "currency", "strategy.cash"}:
            self.config.min_equity_stop = float(self.config.initial_capital) - float(value)
            return
        raise ValueError(f"unsupported risk_max_drawdown type: {type!r}")

    def risk_max_position_size(self, value: float, type: str = "fixed") -> None:
        value_type = str(type).lower()
        if value_type not in {"fixed", "contracts", "shares"}:
            raise ValueError(f"unsupported risk_max_position_size type: {type!r}")
        self.config.max_position_size = float(value)

    def risk_max_intraday_loss(self, value: float, type: str) -> None:
        raise UnsupportedRiskRuleError(
            "strategy.risk.max_intraday_loss is not supported by BacktestEngine"
        )

    def risk_max_intraday_filled_orders(self, value: float, type: str = "fixed") -> None:
        raise UnsupportedRiskRuleError(
            "strategy.risk.max_intraday_filled_orders is not supported by BacktestEngine"
        )
