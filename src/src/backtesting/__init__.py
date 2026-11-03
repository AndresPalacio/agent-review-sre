"""Legacy backtesting names delegated to native services."""

from fin_graph.backtesting import (
    BacktestMetrics as PerformanceMetrics,
)
from fin_graph.backtesting import (
    BacktestRequest,
    BacktestResult,
    run_v1_backtest,
)

BacktestEngine = run_v1_backtest

__all__ = [
    "BacktestEngine",
    "BacktestRequest",
    "BacktestResult",
    "PerformanceMetrics",
    "run_v1_backtest",
]
