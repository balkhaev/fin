"""Typed observability errors exposed by the read-only control plane."""


class DataUnavailableError(RuntimeError):
    """Required public market data could not be retrieved completely."""
