"""Fast/slow simple-moving-average trend strategy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar

from fin_graph.domain.market import Price
from fin_graph.quant.signals.base import JsonValue
from fin_graph.strategies.base import AlgorithmSignal, AlgorithmStrategy
from fin_graph.strategies.catalog import (
    AlgorithmParameter,
    AlgorithmSpecification,
    ParameterValue,
)


class SMACrossoverStrategy(AlgorithmStrategy):
    specification: ClassVar[AlgorithmSpecification] = AlgorithmSpecification(
        key="sma_crossover",
        name="SMA crossover",
        description="Follows trends by comparing a fast and a slow simple moving average.",
        category="trend",
        version="1.0.0",
        parameters={
            "fast_window": AlgorithmParameter(
                type="integer",
                label="Fast average",
                description="Number of daily sessions in the fast moving average.",
                default=20,
                minimum=2,
                maximum=100,
            ),
            "slow_window": AlgorithmParameter(
                type="integer",
                label="Slow average",
                description="Number of daily sessions in the slow moving average.",
                default=50,
                minimum=5,
                maximum=300,
            ),
        },
        warnings=("Signals remain flat until the slow average has enough data.",),
    )

    @classmethod
    def validate_parameters(cls, parameters: Mapping[str, ParameterValue]) -> None:
        if int(parameters["fast_window"]) >= int(parameters["slow_window"]):
            raise ValueError("fast_window must be smaller than slow_window")

    def generate(
        self, data: Sequence[Price], parameters: Mapping[str, JsonValue] | None = None
    ) -> Sequence[AlgorithmSignal]:
        import pandas as pd  # type: ignore[import-untyped]

        values = dict(self.parameters)
        if parameters:
            values.update(parameters)  # type: ignore[arg-type]
        fast_window, slow_window = int(values["fast_window"]), int(values["slow_window"])
        self.validate_parameters(values)
        closes = pd.Series([item.effective_close for item in data])
        fast = closes.rolling(fast_window).mean()
        slow = closes.rolling(slow_window).mean()
        positions = ((fast > slow).astype(float) - (fast < slow).astype(float)).where(
            slow.notna(), 0
        )
        return self._signals(data, positions.tolist())
