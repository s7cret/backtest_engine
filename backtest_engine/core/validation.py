from __future__ import annotations
from .deterministic_hash import sha256_obj
from backtest_engine.models import BarSeries, Diagnostic
from backtest_engine.errors import BarValidationError


def infer_price_tick(series: BarSeries, *, sample_size: int = 100) -> float | None:
    places = 0
    sample = min(len(series), sample_size)
    for i in range(sample):
        b = series.get_bar(i)
        for value in (b.open, b.high, b.low, b.close):
            text = (f"{value:.10f}").rstrip("0").rstrip(".")
            if "." in text:
                places = max(places, len(text.rsplit(".", 1)[1]))
    return 10.0 ** (-places) if places else 1.0


def validate_bars(
    series: BarSeries, duplicate_policy: str = "error"
) -> tuple[BarSeries, list[Diagnostic]]:
    diags = []
    seen = {}
    keep = []
    last = None
    for i in range(len(series)):
        b = series.get_bar(i)
        msg = None
        if last is not None and b.time < last:
            msg = "bars are not sorted by time"
        if b.high < max(b.open, b.close, b.low):
            msg = "invalid OHLC high"
        if b.low > min(b.open, b.close, b.high):
            msg = "invalid OHLC low"
        if b.volume is not None and b.volume < 0:
            msg = "negative volume"
        if msg:
            raise BarValidationError(f"INVALID_BAR at {i}: {msg}")
        if b.time in seen:
            if duplicate_policy == "error":
                raise BarValidationError(f"Duplicate bar time {b.time}")
            if duplicate_policy == "keep_first":
                continue
            if duplicate_policy == "keep_last":
                keep[seen[b.time]] = False
        seen[b.time] = len(keep)
        keep.append(True)
        last = b.time
    bars = [series.get_bar(i) for i, k in enumerate(keep) if k]
    return BarSeries.from_bars(bars), diags


def data_fingerprint(series: BarSeries) -> str:
    return sha256_obj(
        {
            "time": list(series.time),
            "open": list(series.open),
            "high": list(series.high),
            "low": list(series.low),
            "close": list(series.close),
            "volume": None if series.volume is None else list(series.volume),
        }
    )
