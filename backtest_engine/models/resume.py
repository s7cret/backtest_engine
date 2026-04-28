from dataclasses import dataclass, field


@dataclass(frozen=True)
class BacktestResumeState:
    bar_index: int
    config_snapshot_hash: str
    strategy_state: object | None = None
    runtime_state: object | None = None
    order_book_state: object | None = None
    broker_state: object | None = None
    statistics_state: object | None = None
    random_state: object | None = None
    metadata: dict = field(default_factory=dict)
