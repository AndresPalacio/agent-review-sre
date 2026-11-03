"""Wilder RSI mean-reversion strategy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar

from fin_graph.domain.market import Price
from fin_graph.quant.signals.base import JsonValue
from fin_graph.strategies.base import AlgorithmSignal, AlgorithmStrategy
from fin_graph.strategies.catalog import AlgorithmParameter, AlgorithmSpecification, ParameterValue


class RSIMeanReversionStrategy(AlgorithmStrategy):
    specification: ClassVar[AlgorithmSpecification] = AlgorithmSpecification(
        key="rsi_mean_reversion",
        name="RSI mean reversion",
        description="Looks for rebounds from oversold levels and pullbacks from overbought levels.",
        category="mean-reversion",
        version="1.0.0",
        parameters={
            "period": AlgorithmParameter(
                type="integer",
                label="RSI period",
                description="Number of daily sessions used to smooth gains and losses.",
                default=14,
                minimum=2,
                maximum=100,
            ),
            "oversold": AlgorithmParameter(
                type="number",
                label="Oversold level",
                description="RSI below this level produces a long signal.",
                default=30.0,
                minimum=1,
                maximum=49,
            ),
            "overbought": AlgorithmParameter(
                type="number",
                label="Overbought level",
                description="RSI above this level produces a short signal.",
                default=70.0,
                minimum=51,
                maximum=99,
            ),
        },
        warnings=("RSI thresholds can remain inactive during persistent trends.",),
    )

    @classmethod
    def validate_parameters(cls, parameters: Mapping[str, ParameterValue]) -> None:
        if float(parameters["oversold"]) >= float(parameters["overbought"]):
            raise ValueError("oversold must be smaller than overbought")

    def generate(
        self, data: Sequence[Price], parameters: Mapping[str, JsonValue] | None = None
    ) -> Sequence[AlgorithmSignal]:
        import pandas as pd  # type: ignore[import-untyped]

        values = dict(self.parameters)
        if parameters:
            values.update(parameters)  # type: ignore[arg-type]
        self.validate_parameters(values)
        period = int(values["period"])
        closes = pd.Series([item.effective_close for item in data])
        delta = closes.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        relative = gain / loss
        rsi = 100 - 100 / (1 + relative)
        rsi = rsi.mask((loss == 0) & (gain > 0), 100)
        rsi = rsi.mask((loss == 0) & (gain == 0), 50)
        positions = pd.Series(0.0, index=closes.index)
        positions[rsi < float(values["oversold"])] = 1
        positions[rsi > float(values["overbought"])] = -1
        return self._signals(data, positions.tolist())
