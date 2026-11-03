"""Historical code-strategy catalog backed by the native allowlist."""

from fin_graph.strategies.catalog import (
    AlgorithmParameter,
    AlgorithmSpecification,
    ParameterType,
    ParameterValue,
    strategy_catalog,
)

ALGORITHM_REGISTRY = strategy_catalog()
AlgorithmSummary = AlgorithmSpecification

__all__ = [
    "ALGORITHM_REGISTRY",
    "AlgorithmParameter",
    "AlgorithmSpecification",
    "AlgorithmSummary",
    "ParameterType",
    "ParameterValue",
    "strategy_catalog",
]
