"""Read-only discovery routes for the native allowlisted strategy catalog."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from fin_graph.adapters.reference_app.schemas import AlgorithmSpecification, AlgorithmSummary
from fin_graph.strategies.catalog import strategy_catalog

router = APIRouter(prefix="/v2/algorithms")


def _specification(key: str) -> AlgorithmSpecification:
    factory = strategy_catalog().get(key)
    if factory is None:
        raise HTTPException(
            404,
            detail={"code": "algorithm-not-found", "message": f"Unknown algorithm {key!r}"},
        )
    return AlgorithmSpecification.model_validate(factory.specification.model_dump(mode="json"))


@router.get("", response_model=list[AlgorithmSummary])
async def list_algorithms() -> list[AlgorithmSummary]:
    return [
        AlgorithmSummary(
            key=spec.key,
            name=spec.name,
            description=spec.description,
            category=spec.category,
            version=spec.version,
            parameter_count=len(spec.parameters),
        )
        for spec in (_specification(key) for key in strategy_catalog())
    ]


@router.get("/{algorithm_key}", response_model=AlgorithmSpecification)
async def get_algorithm(algorithm_key: str) -> AlgorithmSpecification:
    return _specification(algorithm_key)
