from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class EntryOrderPayload:
    id: str
    direction: str
    qty: float | None = None
    limit: float | None = None
    stop: float | None = None
    oca_name: str | None = None
    oca_type: str | None = None
    comment: str | None = None
    alert_message: str | None = None
    disable_alert: bool = False

    def __post_init__(self) -> None:
        from backtest_engine.core.order_metadata import validate_metadata
        validate_metadata(self)


@dataclass(frozen=True, slots=True)
class ExitPayload:
    id: str
    from_entry: str | None = None
    qty: float | None = None
    qty_percent: float | None = None
    limit: float | None = None
    stop: float | None = None
    profit: float | None = None
    loss: float | None = None
    trail_price: float | None = None
    trail_points: float | None = None
    trail_offset: float | None = None
    oca_name: str | None = None
    oca_type: str | None = None
    comment: str | None = None
    price_pair_policy: str = "absolute_first"
    alert_message: str | None = None
    disable_alert: bool = False
    comment_profit: str | None = None
    comment_loss: str | None = None
    comment_trailing: str | None = None
    alert_profit: str | None = None
    alert_loss: str | None = None
    alert_trailing: str | None = None

    def __post_init__(self) -> None:
        from backtest_engine.core.order_metadata import validate_metadata
        validate_metadata(self)
        if self.price_pair_policy not in ("absolute_first", "first_trigger"):
            raise ValueError("invalid exit price_pair_policy")
        from backtest_engine.core.exit_prices import _optional_number
        names = ("trail_price", "trail_points", "trail_offset")
        values = {name: _optional_number(getattr(self, name), name) for name in names}
        for name, value in values.items():
            object.__setattr__(self, name, value)
        if any(value is not None for value in values.values()):
            if values["trail_offset"] is None or (
                values["trail_price"] is None and values["trail_points"] is None
            ):
                raise ValueError("trailing exit requires trail_offset and an activation level")
            if values["trail_offset"] < 0:
                raise ValueError("trail_offset must be nonnegative")



@dataclass(frozen=True, slots=True)
class ClosePayload:
    id: str | None = None
    qty: float | None = None
    qty_percent: float | None = None
    immediately: bool = False
    comment: str | None = None
    alert_message: str | None = None
    disable_alert: bool = False

    def __post_init__(self) -> None:
        from backtest_engine.core.order_metadata import validate_metadata
        validate_metadata(self)


@dataclass(frozen=True, slots=True)
class CancelPayload:
    id: str | None = None


StrategyCommandPayload = EntryOrderPayload | ExitPayload | ClosePayload | CancelPayload


@dataclass(frozen=True, slots=True)
class StrategyCommand:
    name: str
    payload: StrategyCommandPayload

    @property
    def kwargs(self) -> dict[str, Any]:
        payload = asdict(self.payload)
        if self.name in {"close_all", "cancel_all"} and payload.get("id") is None:
            payload.pop("id", None)
        return payload


@dataclass
class CommandBuffer:
    commands: list[StrategyCommand] = field(default_factory=list)

    def add(self, name: str, **kwargs: Any) -> None:
        self.commands.append(StrategyCommand(name, _payload_for_command(name, kwargs)))

    def drain(self) -> list[StrategyCommand]:
        out = self.commands
        self.commands = []
        return out


def _payload_for_command(name: str, kwargs: dict[str, Any]) -> StrategyCommandPayload:
    if name in {"entry", "order"}:
        return EntryOrderPayload(**kwargs)
    if name == "exit":
        return ExitPayload(**kwargs)
    if name in {"close", "close_all"}:
        return ClosePayload(**kwargs)
    if name in {"cancel", "cancel_all"}:
        return CancelPayload(**kwargs)
    raise ValueError(f"unsupported strategy command: {name!r}")
