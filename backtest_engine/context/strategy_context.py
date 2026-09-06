from __future__ import annotations
from dataclasses import dataclass
from .command_buffer import CommandBuffer
from .strategy_state_view import StrategyStateView
from backtest_engine.errors import UnsupportedRiskRuleError


@dataclass(frozen=True, slots=True)
class RiskRule:
    name: str
    value: float | None = None
    value_type: str | None = None
    direction: str | None = None


class StrategyContext:
    def __init__(self, config: object, state: StrategyStateView | None = None):
        self.config = config
        self.state = state or StrategyStateView()
        self.buffer = CommandBuffer()
        self.risk_rules: list[RiskRule] = []

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
        *,
        alert_message: str | None = None,
        disable_alert: bool = False,
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
            alert_message=alert_message,
            disable_alert=disable_alert,
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
        *,
        alert_message: str | None = None,
        disable_alert: bool = False,
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
            alert_message=alert_message,
            disable_alert=disable_alert,
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
        *,
        price_pair_policy: str = "absolute_first",
        alert_message: str | None = None,
        disable_alert: bool = False,
        comment_profit: str | None = None,
        comment_loss: str | None = None,
        comment_trailing: str | None = None,
        alert_profit: str | None = None,
        alert_loss: str | None = None,
        alert_trailing: str | None = None,
    ) -> None:
        self.buffer.add(
            "exit",
            id=id,
            from_entry=None if from_entry == "" else from_entry,
            qty=qty,
            qty_percent=qty_percent,
            limit=limit,
            stop=stop,
            profit=profit,
            loss=loss,
            trail_price=trail_price,
            trail_points=trail_points,
            trail_offset=trail_offset,
            price_pair_policy=price_pair_policy,
            oca_name=oca_name,
            oca_type=oca_type,
            comment=comment,
            alert_message=alert_message,
            disable_alert=disable_alert,
            comment_profit=comment_profit,
            comment_loss=comment_loss,
            comment_trailing=comment_trailing,
            alert_profit=alert_profit,
            alert_loss=alert_loss,
            alert_trailing=alert_trailing,
        )

    def close(
        self,
        id: str,
        qty: float | None = None,
        qty_percent: float | None = None,
        immediately: bool = False,
        comment: str | None = None,
        *,
        alert_message: str | None = None,
        disable_alert: bool = False,
    ) -> None:
        self.buffer.add(
            "close",
            id=id,
            qty=qty,
            qty_percent=qty_percent,
            immediately=immediately,
            comment=comment,
            alert_message=alert_message,
            disable_alert=disable_alert,
        )

    def close_all(
        self, immediately: bool = False, comment: str | None = None, *,
        alert_message: str | None = None, disable_alert: bool = False,
    ) -> None:
        self.buffer.add("close_all", immediately=immediately, comment=comment,
                        alert_message=alert_message, disable_alert=disable_alert)

    def cancel(self, id: str) -> None:
        self.buffer.add("cancel", id=id)

    def cancel_all(self) -> None:
        self.buffer.add("cancel_all")

    def risk_allow_entry_in(self, direction: str) -> None:
        value = str(direction).lower()
        if value in {"long", "strategy.direction.long"}:
            self.risk_rules.append(RiskRule("allow_entry_in", direction="long"))
            return
        if value in {"short", "strategy.direction.short"}:
            self.risk_rules.append(RiskRule("allow_entry_in", direction="short"))
            return
        if value in {"all", "both", "strategy.direction.all"}:
            self.risk_rules.append(RiskRule("allow_entry_in", direction="all"))
            return
        raise ValueError(f"unsupported risk_allow_entry_in direction: {direction!r}")

    def risk_max_drawdown(self, value: float, type: str) -> None:
        value_type = str(type).lower()
        if value_type in {"percent", "percent_of_equity", "strategy.percent_of_equity"}:
            self.risk_rules.append(
                RiskRule("max_drawdown", float(value), "percent_of_equity")
            )
            return
        if value_type in {"cash", "currency", "strategy.cash"}:
            self.risk_rules.append(RiskRule("max_drawdown", float(value), "cash"))
            return
        raise ValueError(f"unsupported risk_max_drawdown type: {type!r}")

    def risk_max_position_size(self, value: float, type: str = "fixed") -> None:
        value_type = str(type).lower()
        if value_type not in {"fixed", "contracts", "shares"}:
            raise ValueError(f"unsupported risk_max_position_size type: {type!r}")
        from backtest_engine.core.risk_rules import validate_position_limit

        self.risk_rules.append(
            RiskRule("max_position_size", validate_position_limit(value), "fixed")
        )

    def risk_max_intraday_loss(self, value: float, type: str) -> None:
        raise UnsupportedRiskRuleError(
            "strategy.risk.max_intraday_loss is not supported by BacktestEngine"
        )

    def risk_max_intraday_filled_orders(
        self, value: float, type: str = "fixed"
    ) -> None:
        raise UnsupportedRiskRuleError(
            "strategy.risk.max_intraday_filled_orders is not supported by BacktestEngine"
        )

    def drain_risk_rules(self) -> list[RiskRule]:
        rules = self.risk_rules
        self.risk_rules = []
        return rules
