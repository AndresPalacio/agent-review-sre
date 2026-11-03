"""Boundary that preserves the reference application's public interfaces."""

from fin_graph.adapters.reference_app.models import (
    ReferenceAnalystSignal,
    ReferenceHedgeFundRequest,
    ReferenceHedgeFundResult,
    ReferencePortfolioDecision,
)
from fin_graph.adapters.reference_app.portfolio import build_reference_portfolio_result

__all__ = [
    "ReferenceAnalystSignal",
    "ReferenceHedgeFundRequest",
    "ReferenceHedgeFundResult",
    "ReferencePortfolioDecision",
    "build_reference_portfolio_result",
]
