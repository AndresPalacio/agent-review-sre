"""Read-only Prometheus health checks with deterministic assessment and digest."""

from __future__ import annotations

import math
from typing import Any, Literal, Protocol, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field, field_validator

HealthStatus = Literal["healthy", "anomalous", "unknown"]
Operator = Literal["gt", "gte", "lt", "lte"]


class HealthCheckDefinition(BaseModel):
    """One instant Prometheus expression and the boundary that signals an anomaly."""

    id: str = Field(min_length=1, max_length=100)
    expression: str = Field(min_length=1, max_length=2048)
    operator: Operator
    threshold: float = Field(allow_inf_nan=False)

    @field_validator("id", "expression")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


class HealthCheckResult(BaseModel):
    """Small persisted result; probe error details and raw responses are omitted."""

    id: str
    status: HealthStatus
    value: float | None = None
    reason: str = ""
    explanation: str | None = None


class InstantProbe(Protocol):
    async def query(self, expression: str) -> Any: ...


class HealthCheckState(TypedDict, total=False):
    definitions: list[HealthCheckDefinition]
    recent_results: list[HealthCheckResult]
    results: list[HealthCheckResult]
    escalate: bool
    digest: str


def _sample(payload: Any) -> float | None:
    """Accept only an unambiguous finite scalar or single-vector instant sample."""
    if not isinstance(payload, dict):
        return None
    result_type = payload.get("resultType")
    result = payload.get("result")
    if result_type == "scalar":
        sample = result
    elif result_type == "vector" and isinstance(result, list) and len(result) == 1:
        item = result[0]
        sample = item.get("value") if isinstance(item, dict) else None
    else:
        return None
    if not isinstance(sample, (list, tuple)) or len(sample) != 2:
        return None
    raw = sample[1]
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _anomalous(value: float, definition: HealthCheckDefinition) -> bool:
    threshold = definition.threshold
    return {
        "gt": value > threshold,
        "gte": value >= threshold,
        "lt": value < threshold,
        "lte": value <= threshold,
    }[definition.operator]


def make_health_check_graph(
    probe: InstantProbe, model: BaseChatModel | None = None
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile a read-only graph; only current anomalies request scheduler escalation."""

    async def run_checks(state: HealthCheckState) -> dict[str, Any]:
        results: list[HealthCheckResult] = []
        for raw in state.get("definitions", []):
            definition = HealthCheckDefinition.model_validate(raw)
            try:
                value = _sample(await probe.query(definition.expression))
            except Exception:
                results.append(
                    HealthCheckResult(id=definition.id, status="unknown", reason="probe error")
                )
                continue
            if value is None:
                results.append(
                    HealthCheckResult(id=definition.id, status="unknown", reason="no usable sample")
                )
            else:
                results.append(HealthCheckResult(id=definition.id, status="healthy", value=value))
        return {"results": results}

    async def assess(state: HealthCheckState) -> dict[str, Any]:
        assessed: list[HealthCheckResult] = []
        for raw_definition, result in zip(
            state.get("definitions", []), state["results"], strict=True
        ):
            definition = HealthCheckDefinition.model_validate(raw_definition)
            if result.value is None:
                assessed.append(result)
                continue
            status: HealthStatus = (
                "anomalous" if _anomalous(result.value, definition) else "healthy"
            )
            reason = "threshold crossed" if status == "anomalous" else "within threshold"
            explanation: str | None = None
            if model is not None and status == "anomalous":
                try:
                    message = await model.ainvoke(
                        [
                            HumanMessage(
                                content=(
                                    "Explain this metric anomaly briefly. "
                                    f"{definition.id} value={result.value}, "
                                    f"operator={definition.operator}, "
                                    f"threshold={definition.threshold}."
                                )
                            )
                        ]
                    )
                    if isinstance(message.content, str):
                        explanation = message.content[:300]
                except Exception:
                    pass
            assessed.append(
                HealthCheckResult(
                    id=result.id,
                    status=status,
                    value=result.value,
                    reason=reason,
                    explanation=explanation,
                )
            )
        return {"results": assessed}

    def escalate(state: HealthCheckState) -> dict[str, bool]:
        return {"escalate": any(result.status == "anomalous" for result in state["results"])}

    def digest(state: HealthCheckState) -> dict[str, str]:
        recent = [
            HealthCheckResult.model_validate(item) for item in state.get("recent_results", [])
        ]
        combined = [*recent, *state["results"]]
        counts = {
            status: sum(item.status == status for item in combined)
            for status in ("healthy", "anomalous", "unknown")
        }
        lines = [
            f"Health digest: {counts['healthy']} healthy, "
            f"{counts['anomalous']} anomalous, {counts['unknown']} unknown."
        ]
        lines.extend(f"- {item.id}: {item.status} ({item.reason})" for item in combined)
        return {"digest": "\n".join(lines)}

    builder = StateGraph(HealthCheckState)
    builder.add_node("run_checks", run_checks)
    builder.add_node("assess", assess)
    builder.add_node("escalate", escalate)
    builder.add_node("digest", digest)
    builder.add_edge(START, "run_checks")
    builder.add_edge("run_checks", "assess")
    builder.add_edge("assess", "escalate")
    builder.add_edge("escalate", "digest")
    builder.add_edge("digest", END)
    return builder.compile()
