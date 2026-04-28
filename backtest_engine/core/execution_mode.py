from __future__ import annotations

from enum import StrEnum
from typing import Literal

ExecutionModeName = Literal['debug', 'normal', 'fast', 'ultra_fast']


class ExecutionMode(StrEnum):
    DEBUG = 'debug'
    NORMAL = 'normal'
    FAST = 'fast'
    ULTRA_FAST = 'ultra_fast'


def normalize_execution_mode(value: str | ExecutionMode) -> ExecutionMode:
    """Return a validated execution mode enum."""
    try:
        return value if isinstance(value, ExecutionMode) else ExecutionMode(value)
    except ValueError as exc:
        raise ValueError(f'unknown execution mode: {value!r}') from exc


def is_debug_mode(value: str | ExecutionMode) -> bool:
    return normalize_execution_mode(value) is ExecutionMode.DEBUG


def is_fast_mode(value: str | ExecutionMode) -> bool:
    return normalize_execution_mode(value) in {ExecutionMode.FAST, ExecutionMode.ULTRA_FAST}
