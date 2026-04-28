from dataclasses import dataclass, field
from backtest_engine.models import Diagnostic
@dataclass
class ComparisonReport:
    matched: bool
    diagnostics: list[Diagnostic]=field(default_factory=list)
