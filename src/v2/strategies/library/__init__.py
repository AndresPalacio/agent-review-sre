from fin_graph.strategies.base import AlgorithmStrategy as BaseSignalEngine
from fin_graph.strategies.catalog import strategy_catalog

ALGORITHM_REGISTRY = strategy_catalog()

__all__ = ["ALGORITHM_REGISTRY", "BaseSignalEngine"]
