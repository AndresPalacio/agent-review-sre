"""Directional price momentum with a neutral band."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar

from fin_graph.domain.market import Price
from fin_graph.quant.signals.base import JsonValue
from fin_graph.strategies.base import AlgorithmSignal, AlgorithmStrategy
from fin_graph.strategies.catalog import AlgorithmParameter, AlgorithmSpecification


class MomentumStrategy(AlgorithmStrategy):
    specification: ClassVar[AlgorithmSpecification] = AlgorithmSpecification(
        key="momentum",
        name="Price momentum",
        description="Takes long or short exposure when lookback returns leave a neutral band.",
        category="momentum",
        version="1.0.0",
        parameters={
            "lookback": AlgorithmParameter(
                type="integer",
                label="Lookback",
                description="Number of daily sessions used to measure price momentum.",
                default=63,
                minimum=2,
                maximum=252,
            ),
            "threshold": AlgorithmParameter(
                type="number",
                label="Neutral threshold",
                description="Minimum absolute return required before taking a position.",
                default=0.02,
                minimum=0,
                maximum=0.5,
            ),
        },
        warnings=("Momentum can reverse sharply during regime changes.",),
    )

    def generate(
        self, data: Sequence[Price], parameters: Mapping[str, JsonValue] | None = None
    ) -> Sequence[AlgorithmSignal]:
        import pandas as pd  # type: ignore[import-untyped]

        values = dict(self.parameters)
        if parameters:
            values.update(parameters)  # type: ignore[arg-type]
        closes = pd.Series([item.effective_close for item in data])
        momentum = closes.pct_change(int(values["lookback"]))
        positions = pd.Series(0.0, index=closes.index)
        positions[momentum > float(values["threshold"])] = 1
        positions[momentum < -float(values["threshold"])] = -1
        return self._signals(data, positions.tolist())
