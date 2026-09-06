"""Executable RC6 strategy surface, shared by host admission and dispatch.

This registry declares handlers, not TradingView parity. Unsupported risk rules,
trade-indexed accessors and exit variants must not pass host admission silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from pinelib import na

OWNER = "backtest-engine"
DELEGATION_SCHEMA_ID = "openpine.backtest.engine.v1"


@dataclass(frozen=True, slots=True)
class StrategyCommandSpec:
    name: str
    parameters: tuple[str, ...]
    required: tuple[str, ...]
    unsupported_parameters: frozenset[str] = frozenset()

    @property
    def symbol_id(self) -> str:
        return "pine:function:" + self.name

    @property
    def overload_id(self) -> str:
        return self.symbol_id + "#canonical"

    def bind(self, positional: tuple | list, named: Mapping, pine_version: int) -> dict:
        if type(pine_version) is not int or not 1 <= pine_version <= 6:
            raise ValueError("strategy host requires an exact Pine version 1..6")
        if not isinstance(positional, (tuple, list)) or not isinstance(named, Mapping):
            raise ValueError("delegated strategy positional/named arguments are malformed")
        # Historical optional positional layouts changed. Until the producer
        # exposes the exact historical signature, do not guess the binding.
        safe_prefix = {
            "strategy.entry": 8,
            "strategy.order": 8,
            "strategy.exit": 12,
            "strategy.close": 1,
            "strategy.close_all": 0,
            "strategy.cancel": 1,
            "strategy.cancel_all": 0,
        }.get(self.name, len(self.parameters))
        if pine_version < 6 and len(positional) > safe_prefix:
            raise ValueError(f"{self.name}: historical tail arguments must be named")
        if len(positional) > len(self.parameters):
            raise ValueError("delegated strategy invocation has too many positional arguments")
        allowed = (
            set(self.parameters)
            | self.unsupported_parameters
            | (
                {"when"}
                if pine_version < 6 and not self.name.startswith("strategy.risk.")
                else set()
            )
        )
        if any(type(key) is not str or key not in allowed for key in named):
            raise ValueError(f"{self.name}: unknown named argument (when is unavailable in v6)")
        bound = dict(zip(self.parameters, positional))
        if set(bound).intersection(named):
            raise ValueError("delegated strategy invocation repeats an argument")
        bound.update(named)
        if missing := set(self.required).difference(bound):
            raise ValueError(f"{self.name} requires {', '.join(sorted(missing))}")
        if rejected := self.unsupported_parameters.intersection(bound):
            raise ValueError(f"{self.name}: unsupported host parameters {sorted(rejected)}")
        if self.name == "strategy.exit":
            from backtest_engine.core.order_metadata import EXIT_METADATA_FIELDS

            if pine_version < 5 and set(bound).intersection(EXIT_METADATA_FIELDS):
                raise ValueError("per-leg metadata requires Pine v5 or v6")
        return bound


def validate_exit_shape(active: set[str]) -> None:
    """Validate known-present arguments after NA normalization, not signature slots.

    The host supplies a conservative set from literal generated arguments; replay
    supplies actual non-NA values. Dynamic unsupported forms still fail at runtime.
    """
    trailing = active.intersection({"trail_price", "trail_points", "trail_offset"})
    if trailing and ("trail_offset" not in trailing or not trailing.intersection({"trail_price", "trail_points"})):
        raise ValueError("trailing exit requires trail_offset and an activation level")
    if trailing and active.intersection({"stop", "loss"}):
        raise ValueError("fixed stop plus trailing arbitration is not yet supported by this host")


_ENTRY = (
    "id",
    "direction",
    "qty",
    "limit",
    "stop",
    "oca_name",
    "oca_type",
    "comment",
    "alert_message",
    "disable_alert",
)
_EXIT = (
    "id",
    "from_entry",
    "qty",
    "qty_percent",
    "profit",
    "limit",
    "loss",
    "stop",
    "trail_price",
    "trail_points",
    "trail_offset",
    "oca_name",
    "comment",
    "comment_profit",
    "comment_loss",
    "comment_trailing",
    "alert_message",
    "alert_profit",
    "alert_loss",
    "alert_trailing",
    "disable_alert",
)
_COMMANDS = (
    StrategyCommandSpec("strategy.risk.allow_entry_in", ("value",), ("value",)),
    StrategyCommandSpec("strategy.risk.max_position_size", ("contracts",), ("contracts",)),
    StrategyCommandSpec("strategy.entry", _ENTRY, ("id", "direction")),
    StrategyCommandSpec("strategy.order", _ENTRY, ("id", "direction")),
    StrategyCommandSpec(
        "strategy.close",
        ("id", "comment", "qty", "qty_percent", "alert_message", "immediately", "disable_alert"),
        ("id",),
    ),
    StrategyCommandSpec(
        "strategy.close_all", ("comment", "alert_message", "immediately", "disable_alert"), ()
    ),
    StrategyCommandSpec("strategy.cancel", ("id",), ("id",)),
    StrategyCommandSpec("strategy.cancel_all", (), ()),
    # The broker admits exits for open trades and pending price/market entries.
    # Omitted/empty from_entry uses the versioned all-entry scope.
    # Per-leg metadata uses the published flat 2.6 contract.
    StrategyCommandSpec(
        "strategy.exit",
        _EXIT,
        ("id",),
        frozenset(
            {
                "oca_type",
            }
        ),
    ),
)
STRATEGY_COMMANDS = MappingProxyType({spec.name: spec for spec in _COMMANDS})
STRATEGY_CONSTANTS = frozenset(
    {
        "strategy.cash",
        "strategy.commission.cash_per_contract",
        "strategy.commission.cash_per_order",
        "strategy.commission.percent",
        "strategy.direction.all",
        "strategy.direction.long",
        "strategy.direction.short",
        "strategy.fixed",
        "strategy.long",
        "strategy.oca.cancel",
        "strategy.oca.none",
        "strategy.oca.reduce",
        "strategy.percent_of_equity",
        "strategy.short",
    }
)
# A phase snapshot contains these exact scalars; never reconstruct aggregate
# trade counts from the incremental closed-trade delta of a protocol frame.
_STATE_FIELDS = MappingProxyType(
    {
        "equity": "equity",
        "netprofit": "net_profit",
        "openprofit": "open_profit",
        "grossprofit": "gross_profit",
        "grossloss": "gross_loss",
        "wintrades": "win_trades",
        "losstrades": "loss_trades",
        "eventrades": "even_trades",
        "opentrades": "open_trades",
        "closedtrades": "closed_trades",
        "max_drawdown": "max_drawdown",
        "max_runup": "max_runup",
    }
)
STRATEGY_STATE_VALUES = frozenset(
    "strategy." + name
    for name in (
        *_STATE_FIELDS,
        "position_size",
        "position_avg_price",
        "position_entry_name",
        "initial_capital",
        "account_currency",
    )
)


def strategy_values_from_state(state: Any, config: Any) -> dict[str, object]:
    """Copy one broker callback's scalars without retaining mutable broker handles."""
    values = {"strategy." + name: getattr(state, field) for name, field in _STATE_FIELDS.items()}
    opens = state._open_trades_ref
    values.update(
        {
            "strategy.position_size": state.position_size,
            "strategy.position_avg_price": state.position_avg_price if opens else na,
            "strategy.position_entry_name": str(opens[0].entry_id) if opens else na,
            "strategy.initial_capital": config.initial_capital,
            "strategy.account_currency": config.currency,
        }
    )
    return values


def strategy_values_from_projection(projection: Mapping, config: Any) -> dict[str, object]:
    """Read an already schema/hash-verified canonical broker projection."""
    position = projection["position"]
    direction, qty = position["direction"], float(position["qty"])
    fields = {
        "equity": "equity",
        "netprofit": "realized_pnl",
        "openprofit": "unrealized_pnl",
        "grossprofit": "gross_profit",
        "grossloss": "gross_loss",
        "max_drawdown": "max_drawdown",
        "max_runup": "max_runup",
    }
    values = {"strategy." + name: float(projection[field]) for name, field in fields.items()}
    counts = {
        "wintrades": projection["winning_trades"],
        "losstrades": projection["losing_trades"],
        "eventrades": projection["even_trades"],
    }
    values.update({"strategy." + name: value for name, value in counts.items()})
    values.update(
        {
            "strategy.position_size": -qty
            if direction == "SHORT"
            else qty
            if direction == "LONG"
            else 0.0,
            "strategy.position_avg_price": na
            if position["avg_price"] is None
            else float(position["avg_price"]),
            "strategy.position_entry_name": na
            if position["entry_name"] is None
            else position["entry_name"],
            "strategy.opentrades": len(projection["open_trades"]),
            "strategy.closedtrades": sum(counts.values()),
            "strategy.initial_capital": config.initial_capital,
            "strategy.account_currency": projection["currency"],
        }
    )
    return values
