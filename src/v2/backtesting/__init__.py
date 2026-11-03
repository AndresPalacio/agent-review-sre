"""Historical alpha backtesting names backed by the native async engine."""

from fin_graph.backtesting.quant import (
    AlphaBacktestRequest,
    run_alpha_backtest,
)
from fin_graph.backtesting.quant import (
    AlphaBacktestResult as BacktestResult,
)
from fin_graph.backtesting.quant import (
    AlphaPerformanceMetrics as PerformanceMetrics,
)
from fin_graph.backtesting.quant import (
    AlphaTrade as Trade,
)

BacktestEngine = run_alpha_backtest

__all__ = [
    "AlphaBacktestRequest",
    "BacktestEngine",
    "BacktestResult",
    "PerformanceMetrics",
    "Trade",
    "run_alpha_backtest",
]
