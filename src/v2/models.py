"""Historical v2 model names mapped to canonical native models."""

from fin_graph.domain.orders import ProposedOrder as TradeOrder
from fin_graph.quant.signals.base import Signal

QuantSignals = list[Signal]
PortfolioTarget = dict[str, float]
ExecutionResult = list[TradeOrder]

__all__ = ["ExecutionResult", "PortfolioTarget", "QuantSignals", "Signal", "TradeOrder"]
