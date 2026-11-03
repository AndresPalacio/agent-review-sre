"""Native point-in-time feature builders under historical names."""

from fin_graph.quant.data import FundamentalSnapshot as FundamentalsSnapshot
from fin_graph.quant.features import (
    InsufficientData,
    build_technical_snapshot,
)
from fin_graph.quant.features import (
    build_fundamental_snapshot as build_snapshot,
)

PeriodFundamentals = FundamentalsSnapshot

__all__ = [
    "FundamentalsSnapshot",
    "InsufficientData",
    "PeriodFundamentals",
    "build_snapshot",
    "build_technical_snapshot",
]
