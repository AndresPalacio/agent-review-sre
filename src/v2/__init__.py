"""Installed ``v2`` compatibility namespace backed by :mod:`fin_graph.quant`."""

from v2.models import ExecutionResult, PortfolioTarget, QuantSignals, Signal, TradeOrder

__all__ = ["ExecutionResult", "PortfolioTarget", "QuantSignals", "Signal", "TradeOrder"]
