class BacktestEngineError(Exception):
    pass


class ConfigError(BacktestEngineError):
    pass


class TickReplayDataError(ConfigError):
    """Explicit tick input cannot be replayed without approximation."""


class TickReplayStateError(ConfigError):
    """Strategy/runtime state cannot satisfy deterministic tick rollback."""


class BarValidationError(BacktestEngineError):
    pass


class BarMagnifierUnavailableError(BacktestEngineError):
    pass


class ResumeUnsupportedError(BacktestEngineError):
    pass


class UnsupportedInstrumentModelError(BacktestEngineError):
    pass


class ProviderError(BacktestEngineError):
    pass


class StrategyRuntimeError(BacktestEngineError):
    pass


class UnsupportedRiskRuleError(BacktestEngineError):
    pass
