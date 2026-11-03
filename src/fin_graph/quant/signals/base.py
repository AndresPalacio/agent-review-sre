"""Common alpha-model contract and finite, bounded signal model."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fin_graph.quant.data import MarketDataClient

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class Signal(BaseModel):
    """One point-in-time view, bounded from fully bearish to fully bullish."""

    model_config = ConfigDict(frozen=True)

    model_name: str
    ticker: str
    as_of: date
    value: float = Field(ge=-1, le=1)
    reasoning: str = ""
    components: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("value", mode="before")
    @classmethod
    def finite_clamped_value(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("signal value must be numeric")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("signal value must be finite")
        return max(-1.0, min(1.0, number))

    @field_validator("components")
    @classmethod
    def finite_components(cls, values: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(value) for value in values.values()):
            raise ValueError("signal components must be finite")
        return values


class AlphaModel(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def predict(self, ticker: str, as_of: date, data: MarketDataClient) -> Signal: ...


class QuantModel(AlphaModel):
    @staticmethod
    def safe_float(value: Any, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    @staticmethod
    def percentile_rank(value: float, values: list[float]) -> float:
        return 50.0 if not values else sum(item < value for item in values) / len(values) * 100

    @staticmethod
    def sigmoid(value: float, scale: float = 5.0) -> float:
        return math.tanh(value * scale)


type AlphaModelFactory = Callable[..., AlphaModel]
type AlphaModelRegistry = Mapping[str, AlphaModelFactory]
