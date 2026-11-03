"""Validated explicit registry for code-defined algorithm strategies."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from functools import lru_cache
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fin_graph.strategies.base import AlgorithmStrategy

type ParameterType = Literal["integer", "number", "boolean", "enum"]
type ParameterValue = int | float | bool | str


class AlgorithmParameter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: ParameterType
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    default: ParameterValue
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()

    @model_validator(mode="after")
    def coherent(self) -> AlgorithmParameter:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        if self.type == "integer" and type(self.default) is not int:
            raise ValueError("integer default required")
        if self.type == "number" and (
            isinstance(self.default, bool)
            or not isinstance(self.default, int | float)
            or not math.isfinite(float(self.default))
        ):
            raise ValueError("finite numeric default required")
        if self.type == "boolean" and type(self.default) is not bool:
            raise ValueError("boolean default required")
        if self.type == "enum" and self.default not in self.choices:
            raise ValueError("enum default must be a choice")
        return self


class AlgorithmSpecification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    name: str
    description: str
    category: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    parameters: dict[str, AlgorithmParameter] = Field(default_factory=dict)
    supported_intervals: tuple[str, ...] = ("1D",)
    supported_providers: tuple[str, ...] = ("yahoo",)
    warnings: tuple[str, ...] = ()

    @property
    def defaults(self) -> dict[str, ParameterValue]:
        return {name: definition.default for name, definition in self.parameters.items()}


class AlgorithmFactory:
    def __init__(self, strategy_type: type[AlgorithmStrategy], registry: AlgorithmRegistry) -> None:
        self.strategy_type = strategy_type
        self.registry = registry

    @property
    def specification(self) -> AlgorithmSpecification:
        return self.strategy_type.specification

    def create(self, parameters: Mapping[str, object] | None = None) -> AlgorithmStrategy:
        return self.registry.create(self.specification.key, parameters)


class AlgorithmRegistry:
    def __init__(self, strategy_types: Iterable[type[AlgorithmStrategy]]) -> None:
        self._types: dict[str, type[AlgorithmStrategy]] = {}
        for strategy_type in strategy_types:
            if not issubclass(strategy_type, AlgorithmStrategy):
                raise ValueError("algorithm must inherit AlgorithmStrategy")
            key = strategy_type.specification.key
            if key in self._types:
                raise ValueError(f"duplicate algorithm key: {key}")
            self._types[key] = strategy_type

    def resolve(
        self, key: str, supplied: Mapping[str, object] | None = None
    ) -> dict[str, ParameterValue]:
        try:
            strategy_type = self._types[key]
        except KeyError as exc:
            raise KeyError(f"Unknown algorithm {key!r}") from exc
        definitions = strategy_type.specification.parameters
        provided = supplied or {}
        unknown = sorted(set(provided) - set(definitions))
        if unknown:
            raise ValueError(f"unknown algorithm parameter(s): {unknown}")
        resolved = {
            name: self._resolve_one(name, definition, provided.get(name, definition.default))
            for name, definition in definitions.items()
        }
        strategy_type.validate_parameters(resolved)
        return resolved

    @staticmethod
    def _resolve_one(name: str, definition: AlgorithmParameter, value: object) -> ParameterValue:
        if definition.type == "integer":
            if type(value) is not int:
                raise ValueError(f"parameter {name!r} must be an integer")
            normalized: ParameterValue = value
        elif definition.type == "number":
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"parameter {name!r} must be a finite number")
            normalized = float(value)
        elif definition.type == "boolean":
            if type(value) is not bool:
                raise ValueError(f"parameter {name!r} must be boolean")
            normalized = value
        else:
            if not isinstance(value, str) or value not in definition.choices:
                raise ValueError(f"parameter {name!r} must be one of {definition.choices}")
            normalized = value
        if definition.type in {"integer", "number"}:
            numeric = float(normalized)
            if definition.minimum is not None and numeric < definition.minimum:
                raise ValueError(f"parameter {name!r} must be at least {definition.minimum}")
            if definition.maximum is not None and numeric > definition.maximum:
                raise ValueError(f"parameter {name!r} must be at most {definition.maximum}")
        return normalized

    def create(self, key: str, supplied: Mapping[str, object] | None = None) -> AlgorithmStrategy:
        try:
            strategy_type = self._types[key]
        except KeyError as exc:
            raise KeyError(f"Unknown algorithm {key!r}") from exc
        return strategy_type(**self.resolve(key, supplied))

    def factories(self) -> MappingProxyType[str, AlgorithmFactory]:
        return MappingProxyType(
            {
                key: AlgorithmFactory(strategy_type, self)
                for key, strategy_type in self._types.items()
            }
        )


@lru_cache(maxsize=1)
def strategy_catalog() -> MappingProxyType[str, AlgorithmFactory]:
    from fin_graph.strategies.momentum import MomentumStrategy
    from fin_graph.strategies.rsi_mean_reversion import RSIMeanReversionStrategy
    from fin_graph.strategies.sma_crossover import SMACrossoverStrategy

    return AlgorithmRegistry(
        [SMACrossoverStrategy, RSIMeanReversionStrategy, MomentumStrategy]
    ).factories()
