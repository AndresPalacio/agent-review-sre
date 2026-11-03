"""Application-service compatibility for the reference ai-hedge-fund v1 core."""

from fin_graph.compat.ai_hedge_fund.models import (
    LegacyAnalystSignal,
    LegacyHedgeFundRequest,
    LegacyHedgeFundResult,
    LegacyPortfolioDecision,
)
from fin_graph.compat.ai_hedge_fund.portfolio import build_legacy_portfolio_result

__all__ = [
    "LegacyAnalystSignal",
    "LegacyHedgeFundRequest",
    "LegacyHedgeFundResult",
    "LegacyPortfolioDecision",
    "build_legacy_portfolio_result",
]
