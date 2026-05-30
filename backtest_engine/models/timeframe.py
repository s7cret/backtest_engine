from __future__ import annotations

from backtest_engine.errors import BarValidationError


def infer_close_from_timeframe(parent_open: int, timeframe: str) -> int:
    try:
        from marketdata_provider.contracts import InvalidTimeframeError, parse_timeframe

        parsed = parse_timeframe(timeframe)
    except InvalidTimeframeError as exc:
        raise BarValidationError(
            "parent bar missing time_close and timeframe duration is unknown"
        ) from exc

    if parsed.duration_ms is None:
        raise BarValidationError(
            "parent bar missing time_close and timeframe duration is unknown"
        )
    return parent_open + parsed.duration_ms
