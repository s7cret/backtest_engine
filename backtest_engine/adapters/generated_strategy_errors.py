"""Private bridge exception types for generated strategy adapters."""

from __future__ import annotations


class GeneratedStrategyBridgeError(RuntimeError):
    """Raised when a generated strategy cannot be safely adapted."""


class UnsupportedGeneratedStrategySemantics(GeneratedStrategyBridgeError):
    """Raised for generated/PineLib semantics this bridge will not approximate."""
