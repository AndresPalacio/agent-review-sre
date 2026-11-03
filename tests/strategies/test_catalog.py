from __future__ import annotations

from typing import ClassVar

import pytest

from fin_graph.strategies.base import AlgorithmStrategy
from fin_graph.strategies.catalog import (
    AlgorithmParameter,
    AlgorithmRegistry,
    AlgorithmSpecification,
    strategy_catalog,
)


class Demo(AlgorithmStrategy):
    specification: ClassVar[AlgorithmSpecification] = AlgorithmSpecification(
        key="demo",
        name="Demo",
        description="Test strategy.",
        category="test",
        version="1.0.0",
        parameters={
            "window": AlgorithmParameter(
                type="integer",
                label="Window",
                description="Lookback.",
                default=5,
                minimum=2,
                maximum=20,
            )
        },
    )

    def generate(self, data, parameters=None):
        return ()


def test_catalog_has_exact_reference_keys_specs_and_defaults() -> None:
    catalog = strategy_catalog()
    assert tuple(catalog) == ("sma_crossover", "rsi_mean_reversion", "momentum")
    assert catalog["sma_crossover"].specification.defaults == {
        "fast_window": 20,
        "slow_window": 50,
    }
    assert catalog["rsi_mean_reversion"].specification.defaults == {
        "period": 14,
        "oversold": 30.0,
        "overbought": 70.0,
    }
    assert catalog["momentum"].specification.defaults == {
        "lookback": 63,
        "threshold": 0.02,
    }


def test_registry_rejects_duplicates_unknown_and_invalid_parameters() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        AlgorithmRegistry([Demo, Demo])
    registry = AlgorithmRegistry([Demo])
    with pytest.raises(KeyError, match="Unknown"):
        registry.create("missing")
    with pytest.raises(ValueError, match="unknown"):
        registry.create("demo", {"other": 1})
    with pytest.raises(ValueError, match="window"):
        registry.create("demo", {"window": 1})


@pytest.mark.parametrize(
    ("key", "parameters", "message"),
    [
        ("sma_crossover", {"fast_window": 20, "slow_window": 10}, "fast_window"),
        (
            "rsi_mean_reversion",
            {"oversold": 80, "overbought": 70},
            "oversold",
        ),
        ("momentum", {"threshold": float("nan")}, "finite"),
    ],
)
def test_builtin_cross_parameter_validation(key, parameters, message) -> None:
    with pytest.raises(ValueError, match=message):
        strategy_catalog()[key].create(parameters)
