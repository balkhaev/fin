"""Frozen FIN paper/shadow runtime contracts.

This package deliberately exposes no live execution adapter.
"""

from .models import (
    ExecutionIntent,
    ExecutionPlan,
    FillEvent,
    MarketSnapshot,
    PortfolioState,
    ReconciliationReport,
    SourceObservation,
    StrategySnapshot,
)

__all__ = [
    "ExecutionIntent",
    "ExecutionPlan",
    "FillEvent",
    "MarketSnapshot",
    "PortfolioState",
    "ReconciliationReport",
    "SourceObservation",
    "StrategySnapshot",
]

__version__ = "0.2.0"
