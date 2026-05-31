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


@dataclass(frozen=True, slots=True)
class ClosePayload:
    id: str | None = None
    qty: float | None = None
    qty_percent: float | None = None
    immediately: bool = False
    comment: str | None = None


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
