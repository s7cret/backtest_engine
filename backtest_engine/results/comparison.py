from __future__ import annotations
import csv
from dataclasses import dataclass, field, asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable
from backtest_engine.models import Diagnostic


@dataclass
class ComparisonReport:
    matched: bool
    our_count: int = 0
    reference_count: int = 0
    first_mismatch_index: int | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["diagnostics"] = [asdict(x) for x in self.diagnostics]
        return d


def _row(obj: Any) -> dict[str, Any]:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    return {
        k: getattr(obj, k)
        for k in dir(obj)
        if not k.startswith("_") and not callable(getattr(obj, k))
    }


def load_trades_csv(path: str | Path) -> list[dict[str, Any]]:
    with open(path, newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def compare_trades(
    our_trades: Iterable[Any],
    reference_trades: Iterable[Any],
    *,
    price_tolerance: float = 0.0,
    qty_tolerance: float = 0.0,
) -> ComparisonReport:
    ours = [_row(t) for t in our_trades]
    refs = [_row(t) for t in reference_trades]
    diags: list[Diagnostic] = []
    first: int | None = None
    fields = ("entry_time", "exit_time", "entry_price", "exit_price", "qty", "profit")
    n = min(len(ours), len(refs))
    if len(ours) != len(refs):
        first = n
        diags.append(
            Diagnostic(
                "TRADINGVIEW_COMPARE_MISMATCH",
                "trade count mismatch",
                "warning",
                context={"our_count": len(ours), "reference_count": len(refs)},
            )
        )
    for i in range(n):
        ctx: dict[str, Any] = {"index": i}
        bad = False
        for f in fields:
            if f not in ours[i] or f not in refs[i]:
                continue
            a, b = ours[i][f], refs[i][f]
            tol = (
                qty_tolerance
                if f == "qty"
                else price_tolerance if "price" in f else 0.0
            )
            try:
                mismatch = abs(float(a) - float(b)) > tol
            except (TypeError, ValueError):
                mismatch = str(a) != str(b)
            if mismatch:
                bad = True
                ctx[f] = {"actual": a, "expected": b, "tolerance": tol}
        if bad:
            if first is None:
                first = i
            diags.append(
                Diagnostic(
                    "TRADINGVIEW_COMPARE_MISMATCH",
                    "trade field mismatch",
                    "warning",
                    context=ctx,
                )
            )
            break
    return ComparisonReport(
        matched=not diags,
        our_count=len(ours),
        reference_count=len(refs),
        first_mismatch_index=first,
        summary={"checked": n},
        diagnostics=diags,
    )
