"""Offline contract tests for scheduled health checks."""

from __future__ import annotations

import math
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from fin_graph.agents.testing.scripted_model import ScriptedChatModel
from fin_graph.graphs.health_check import (
    HealthCheckDefinition,
    HealthCheckResult,
    make_health_check_graph,
)


class Probe:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses

    async def query(self, expression: str) -> Any:
        answer = self.responses[expression]
        if isinstance(answer, Exception):
            raise answer
        return answer


def vector(value: str) -> dict[str, Any]:
    return {"resultType": "vector", "result": [{"metric": {}, "value": [1, value]}]}


async def test_threshold_boundary_and_escalation_marker() -> None:
    graph = make_health_check_graph(Probe({"errors": vector("5"), "latency": vector("0.9")}))
    state = await graph.ainvoke(
        {
            "definitions": [
                HealthCheckDefinition(id="errors", expression="errors", operator="gt", threshold=5),
                HealthCheckDefinition(
                    id="latency", expression="latency", operator="gte", threshold=0.9
                ),
            ]
        }
    )

    assert [result.status for result in state["results"]] == ["healthy", "anomalous"]
    assert [result.value for result in state["results"]] == [5.0, 0.9]
    assert state["escalate"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"resultType": "vector", "result": []},
        vector("NaN"),
        vector("+Inf"),
        vector("n/a"),
        {"resultType": "vector", "result": [{"metric": {}}]},
        {"resultType": "vector", "result": [{"value": [1, "1"]}, {"value": [1, "2"]}]},
        {"resultType": "matrix", "result": []},
    ],
)
async def test_missing_or_invalid_sample_is_unknown(payload: dict[str, Any]) -> None:
    graph = make_health_check_graph(Probe({"up": payload}))

    state = await graph.ainvoke(
        {
            "definitions": [
                HealthCheckDefinition(id="up", expression="up", operator="lt", threshold=1)
            ]
        }
    )

    assert state["results"][0].status == "unknown"
    assert state["results"][0].value is None
    assert state["escalate"] is False


async def test_scalar_comparison_and_probe_error_are_isolated() -> None:
    graph = make_health_check_graph(
        Probe({"disk": {"resultType": "scalar", "result": [1, "80"]}, "api": RuntimeError("token")})
    )

    state = await graph.ainvoke(
        {
            "definitions": [
                HealthCheckDefinition(id="disk", expression="disk", operator="lte", threshold=80),
                HealthCheckDefinition(id="api", expression="api", operator="gt", threshold=1),
            ]
        }
    )

    assert [result.status for result in state["results"]] == ["anomalous", "unknown"]
    assert state["results"][1].reason == "probe error"
    assert "token" not in state["digest"]


async def test_digest_summarizes_recent_results_without_model() -> None:
    recent = [
        HealthCheckResult(id="old", status="unknown", reason="no data"),
        HealthCheckResult(id="past", status="anomalous", value=10, reason="threshold exceeded"),
    ]
    graph = make_health_check_graph(Probe({"now": vector("0")}))

    state = await graph.ainvoke(
        {
            "definitions": [
                HealthCheckDefinition(id="now", expression="now", operator="gt", threshold=1)
            ],
            "recent_results": recent,
        }
    )

    assert "1 healthy" in state["digest"]
    assert "1 anomalous" in state["digest"]
    assert "1 unknown" in state["digest"]
    assert "old" in state["digest"]
    assert state["escalate"] is False


def test_definition_rejects_unbounded_or_invalid_parameters() -> None:
    with pytest.raises(ValueError):
        HealthCheckDefinition(id="", expression="up", operator="gt", threshold=1)
    with pytest.raises(ValueError):
        HealthCheckDefinition(id="up", expression="", operator="gt", threshold=1)
    with pytest.raises(ValueError):
        HealthCheckDefinition(id="up", expression="up", operator="gt", threshold=math.nan)


async def test_model_explanation_cannot_change_threshold_decision() -> None:
    model = ScriptedChatModel(responses=[AIMessage(content="This is healthy.")])
    graph = make_health_check_graph(Probe({"errors": vector("10")}), model=model)

    state = await graph.ainvoke(
        {
            "definitions": [
                HealthCheckDefinition(id="errors", expression="errors", operator="gt", threshold=5)
            ]
        }
    )

    assert state["results"][0].status == "anomalous"
    assert state["results"][0].explanation == "This is healthy."
    assert state["escalate"] is True
