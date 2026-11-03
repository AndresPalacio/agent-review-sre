"""Common algorithm signal and strategy contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from fin_graph.domain.market import Price
from fin_graph.quant.signals.base import JsonValue

if TYPE_CHECKING:
    from fin_graph.strategies.catalog import AlgorithmSpecification, ParameterValue


class AlgorithmSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    ticker: str
    action: Literal["long", "short", "flat"]
    strength: float = Field(ge=0, le=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AlgorithmStrategy(ABC):
    specification: ClassVar[AlgorithmSpecification]

    def __init__(self, **parameters: ParameterValue) -> None:
        self.parameters = parameters

    @classmethod
    def validate_parameters(cls, parameters: Mapping[str, ParameterValue]) -> None:
        """Validate relationships between individually valid parameters."""

        return None

    @abstractmethod
    def generate(
        self,
        data: Sequence[Price],
        parameters: Mapping[str, JsonValue] | None = None,
    ) -> Sequence[AlgorithmSignal]: ...

    def _signals(
        self, data: Sequence[Price], positions: Sequence[float]
    ) -> tuple[AlgorithmSignal, ...]:
        if len(data) != len(positions):
            raise ValueError("strategy output length does not match price history")
        ticker = str(self.parameters.get("ticker", ""))
        return tuple(
            AlgorithmSignal(
                timestamp=price.time,
                ticker=ticker,
                action="long" if value > 0 else "short" if value < 0 else "flat",
                strength=min(1.0, abs(float(value))),
                metadata={"position": float(value)},
            )
            for price, value in zip(data, positions, strict=True)
        )
