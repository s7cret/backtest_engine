"""One version-aware configuration boundary shared by hosts and workers.

Only serializable execution settings cross this boundary. It is intentionally
not a generic object deserializer: runtime/providers and local filesystem paths
must be admitted by their own interfaces, never reconstructed from a string.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal
from types import MappingProxyType, UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from openpine_contracts import content_hash, decimal_string

from backtest_engine.config import BacktestConfig
from backtest_engine.errors import ConfigError
from backtest_engine.models.instrument import InstrumentModel

_FIELDS = {field.name: field for field in dataclasses.fields(BacktestConfig)}
_HOST_IDENTITY = frozenset({"exchange", "market_type"})
_LOCAL_ONLY = frozenset(
    {
        "runtime",
        "realtime_tick_provider",
        "realtime_ticks",
        "bar_magnifier_bars",
        "output_dir",
        "tradingview_reference_path",
    }
)
_ALIASES = {"qty_rounding_mode": "qty_rounding"}
_HINTS = get_type_hints(BacktestConfig)


def _identity_value(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigError("configuration numbers must be finite")
        return {"decimal": decimal_string(Decimal(str(value)))}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _identity_value(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ConfigError("configuration object keys must be strings")
        return {key: _identity_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value) if isinstance(value, (set, frozenset)) else value
        return [_identity_value(item) for item in items]
    if value is None or type(value) in (str, int, bool):
        return value
    raise ConfigError(f"configuration contains a nonportable {type(value).__name__}")


def execution_config_values(config: object) -> dict[str, Any]:
    """Extract declared engine settings without lossy ``value or default``.

    Host-only plotting/admission metadata is not an engine setting. All fields
    declared by BacktestConfig are transferred automatically, including warmup,
    currency, result collection and resume policies, rather than a partial list.
    """
    result: dict[str, Any] = {}
    for name in _FIELDS.keys() | _HOST_IDENTITY:
        if hasattr(config, name):
            value = getattr(config, name)
            if name in _LOCAL_ONLY and value is not None:
                raise ConfigError(f"{name} requires its dedicated worker admission interface")
            if isinstance(value, (set, frozenset)):
                value = sorted(value)
            elif dataclasses.is_dataclass(value) and not isinstance(value, type):
                value = dataclasses.asdict(value)
            result[name] = deepcopy(value)
    for old, new in _ALIASES.items():
        if hasattr(config, old):
            if new in result and result[new] != getattr(config, old):
                raise ConfigError(f"conflicting {old} and {new}")
            result[new] = getattr(config, old)
    return result


def _normalized_layer(layer: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for raw_name, value in layer.items():
        name = _ALIASES.get(raw_name, raw_name)
        if name not in _FIELDS and name not in _HOST_IDENTITY:
            raise ConfigError(f"unknown execution setting: {raw_name}")
        if name in values and values[name] != value:
            raise ConfigError(f"conflicting execution aliases for {name}")
        if name in _LOCAL_ONLY and value is not None:
            raise ConfigError(f"{name} requires its dedicated worker admission interface")
        if name == "exit_matching" and type(value) is str:
            value = value.lower()
        hint = _HINTS.get(name)
        allowed = get_args(hint) if get_origin(hint) in (UnionType, Union, Literal) else (hint,)
        if value is None:
            if type(None) not in allowed and name in _FIELDS:
                # Explicit null is not an omitted value. In particular it must
                # not erase a declared margin or quantity with a guessed default.
                raise ConfigError(f"{name} does not accept null")
        elif get_origin(hint) is Literal and value not in allowed:
            raise ConfigError(f"{name} has an unsupported value")
        elif hint is bool and type(value) is not bool:
            raise ConfigError(f"{name} must be a boolean")
        elif int in allowed and float not in allowed and type(value) is not int:
            raise ConfigError(f"{name} must be an integer")
        elif float in allowed:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ConfigError(f"{name} must be a finite number")
        elif str in allowed and type(value) is not str:
            raise ConfigError(f"{name} must be a string")
        if float in allowed and value is not None:
            value = float(value)
        _identity_value(value)  # reject nonportable objects and nonfinite nested values
        values[name] = deepcopy(value)
    return values


def _build_config(values: Mapping[str, Any]) -> BacktestConfig:
    values = deepcopy(dict(values))
    if isinstance(values.get("instrument_model"), Mapping):
        try:
            values["instrument_model"] = InstrumentModel(**values["instrument_model"])
        except (TypeError, ValueError) as error:
            raise ConfigError("invalid instrument_model") from error
    for name in ("required_outputs", "required_metrics"):
        if name in values:
            items = values[name]
            if not isinstance(items, (list, tuple, set, frozenset)) or any(
                type(x) is not str for x in items
            ):
                raise ConfigError(f"{name} must be a collection of strings")
            values[name] = set(items)
    try:
        config = BacktestConfig(**{key: value for key, value in values.items() if key in _FIELDS})
    except (TypeError, ValueError) as error:
        raise ConfigError(f"invalid execution configuration: {error}") from error
    for key in _HOST_IDENTITY:
        if key in values:
            setattr(config, key, values[key])
    return config


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclasses.dataclass(frozen=True)
class EffectiveStrategyConfig:
    """Immutable admitted values/provenance with a content-sensitive identity."""

    _values: Mapping[str, Any] = dataclasses.field(repr=False)
    _origins: Mapping[str, str] = dataclasses.field(repr=False)
    content_hash: str

    @classmethod
    def resolve(
        cls,
        execution: Mapping[str, Any],
        *,
        pine_version: int = 6,
        declaration: Mapping[str, Any] | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> EffectiveStrategyConfig:
        if type(pine_version) is not int or pine_version not in range(1, 7):
            raise ConfigError("pine_version must be in 1..6")
        values: dict[str, Any] = {
            "margin_long": 100.0 if pine_version == 6 else 0.0,
            "margin_short": 100.0 if pine_version == 6 else 0.0,
        }
        origins = {key: f"pine_v{pine_version}_default" for key in values}
        for source, layer in (
            ("declaration", declaration),
            ("execution", execution),
            ("user_override", overrides),
        ):
            if layer is not None:
                normalized = _normalized_layer(layer)
                values.update(normalized)
                origins.update({key: source for key in normalized})
        config = _build_config(values)
        from backtest_engine.core.engine_validation import validate_backtest_config

        validate_backtest_config(config)
        snapshot = execution_config_values(config)
        for key in snapshot:
            origins.setdefault(key, "engine_default")
        identity = content_hash(
            {"values": _identity_value(snapshot)}, schema_id="backtest_engine.effective_config.v1"
        )
        return cls(_freeze(snapshot), MappingProxyType(origins), identity)

    def to_engine_config(self) -> BacktestConfig:
        """Return a detached engine instance; share it with its intent handler."""
        return _build_config(_thaw(self._values))

    @property
    def origins(self) -> Mapping[str, str]:
        return self._origins

    def report(self) -> dict[str, Any]:
        return {
            "schema_id": "backtest_engine.effective_config.v1",
            "content_hash": self.content_hash,
            "values": _thaw(self._values),
            "origins": dict(self.origins),
        }
