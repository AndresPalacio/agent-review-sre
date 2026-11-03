"""Historical strategy backtest types mapped to the native deterministic engine."""

from fin_graph.backtesting.artifacts import SignalPage as AlgorithmSignalPage
from fin_graph.backtesting.strategies import (
    EquityPoint,
    StrategyBacktestMetrics,
    StrategyBacktestOutput,
    run_strategy_backtest,
)
from fin_graph.backtesting.strategies import (
    StrategyBacktestRequest as AlgorithmBacktestConfig,
)
from fin_graph.backtesting.strategies import (
    StrategyBacktestResult as AlgorithmBacktestResult,
)
from fin_graph.backtesting.strategies import (
    StrategySignalPoint as AlgorithmSignalPoint,
)
from fin_graph.backtesting.strategies import (
    StrategySignalSummary as AlgorithmSignalSummary,
)
from fin_graph.backtesting.strategies import (
    StrategyTrade as Trade,
)

__all__ = [
    "AlgorithmBacktestConfig",
    "AlgorithmBacktestResult",
    "AlgorithmSignalPage",
    "AlgorithmSignalPoint",
    "AlgorithmSignalSummary",
    "EquityPoint",
    "StrategyBacktestMetrics",
    "StrategyBacktestOutput",
    "Trade",
    "run_strategy_backtest",
]
