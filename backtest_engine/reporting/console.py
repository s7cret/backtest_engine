from __future__ import annotations

from typing import Any


def render(value: Any, **_: Any) -> str:
    if value is None:
        return ""
    return str(value) + "\n"
