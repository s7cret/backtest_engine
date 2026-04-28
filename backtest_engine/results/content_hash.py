from __future__ import annotations

from typing import Any

from backtest_engine.core.deterministic_hash import sha256_obj


def result_content_hash(
    result: Any, *, include_equity_curve: bool = True, include_events: bool = False
) -> str:
    if hasattr(result, "content_hash"):
        return result.content_hash(
            include_equity_curve=include_equity_curve, include_events=include_events
        )
    if hasattr(result, "to_dict"):
        return sha256_obj(result.to_dict())
    return sha256_obj(result)
