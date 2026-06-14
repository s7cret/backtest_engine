from __future__ import annotations

import re

from backtest_engine.errors import BarValidationError

_FIXED_TIMEFRAME_MS = {
    "S": 1_000,
    "T": 60_000,
    "": 60_000,
    "M": 60_000,
    "H": 3_600_000,
    "D": 86_400_000,
    "W": 604_800_000,
}


def _fallback_duration_ms(timeframe: str) -> int | None:
    text = str(timeframe).strip()
    if not text:
        return None
    match = re.fullmatch(r"(\d+)?([sSmMhHdDwW]?)", text)
    if not match:
        return None
    amount = int(match.group(1) or "1")
    unit = match.group(2)
    if unit == "M":
        # TradingView uses M for months, which are calendar-duration bars and
        # cannot be inferred from an open timestamp without a calendar.
        return None
    base = _FIXED_TIMEFRAME_MS.get(unit.upper())
    return None if base is None else amount * base


def infer_close_from_timeframe(parent_open: int, timeframe: str) -> int:
    try:
        from marketdata_provider.contracts import InvalidTimeframeError, parse_timeframe
    except ImportError:
        duration_ms = _fallback_duration_ms(timeframe)
        if duration_ms is None:
            raise BarValidationError(
                "parent bar missing time_close and timeframe duration is unknown"
            )
        return parent_open + duration_ms

    try:
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
