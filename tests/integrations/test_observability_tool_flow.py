from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from sre_graph.agents.factory import make_agent
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from sre_graph.integrations.alertmanager.client import AlertmanagerClient
from sre_graph.integrations.alertmanager.integration import AlertmanagerIntegration
from sre_graph.integrations.base import Integration
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.datadog.client import DatadogClient
from sre_graph.integrations.datadog.integration import DatadogIntegration
from sre_graph.integrations.grafana.client import GrafanaClient
from sre_graph.integrations.grafana.integration import GrafanaIntegration
from sre_graph.integrations.loki.client import LokiClient
from sre_graph.integrations.loki.integration import LokiIntegration
from sre_graph.integrations.newrelic.client import NewRelicClient
from sre_graph.integrations.newrelic.integration import NewRelicIntegration
from sre_graph.integrations.prometheus.client import PrometheusClient
from sre_graph.integrations.prometheus.integration import MimirIntegration, PrometheusIntegration
from sre_graph.integrations.tempo.client import TempoClient
from sre_graph.integrations.tempo.integration import TempoIntegration


def _case(
    integration_id: str, transport: httpx.MockTransport
) -> tuple[Integration[Any, Any], object, str, dict[str, Any]]:
    url = "https://example.test"
    if integration_id == "prometheus":
        return (
            PrometheusIntegration(),
            PrometheusClient(url, transport=transport),
            "prom_query",
            {"expression": "up"},
        )
    if integration_id == "alertmanager":
        return (
            AlertmanagerIntegration(),
            AlertmanagerClient(url, transport=transport),
            "am_alerts",
            {},
        )
    if integration_id == "grafana":
        return (
            GrafanaIntegration(),
            GrafanaClient(url, transport=transport),
            "grafana_dashboards",
            {"query": "checkout"},
        )
    if integration_id == "loki":
        return (
            LokiIntegration(),
            LokiClient(url, transport=transport),
            "loki_labels",
            {"start": 100, "end": 200},
        )
    if integration_id == "tempo":
        return (
            TempoIntegration(),
            TempoClient(url, transport=transport),
            "tempo_trace",
            {"trace_id": "a" * 32},
        )
    if integration_id == "mimir":
        return (
            MimirIntegration(),
            PrometheusClient(url, vendor="Mimir", transport=transport),
            "mimir_query",
            {"expression": "up"},
        )
    if integration_id == "datadog":
        return (
            DatadogIntegration(),
            DatadogClient(url, "a", "b", transport=transport),
            "dd_monitors",
            {},
        )
    return (
        NewRelicIntegration(),
        NewRelicClient(url, "key", 42, transport=transport),
        "nr_nrql",
        {"query": "SELECT count(*) FROM Transaction"},
    )


def _response(integration_id: str) -> object:
    if integration_id in ("prometheus", "loki", "mimir"):
        return {"status": "success", "data": [{"name": "up"}]}
    if integration_id == "newrelic":
        return {"data": {"actor": {"account": {"nrql": {"results": [{"count": 1}]}}}}}
    if integration_id == "tempo":
        return {"batches": []}
    return [{"id": "one"}]


@pytest.mark.parametrize(
    "integration_id",
    ["prometheus", "alertmanager", "grafana", "loki", "tempo", "mimir", "datadog", "newrelic"],
)
async def test_a_reader_tool_uses_run_context_and_returns_an_artifact(integration_id: str) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=_response(integration_id))
    )
    integration, client, tool_name, args = _case(integration_id, transport)
    model = ScriptedChatModel(
        responses=[tool_call_message(tool_name, args, "c1"), AIMessage("done")]
    )
    agent = make_agent(
        name="reader", model=model, tools=integration.tools(), context_schema=RunContext
    )

    result = await agent.ainvoke(
        {"messages": [HumanMessage("investigate")]},
        context=RunContext(clients={integration_id: client}),
    )

    message = next(item for item in result["messages"] if isinstance(item, ToolMessage))
    assert message.status == "success"
    assert message.artifact is not None
    assert str(message.content)


async def test_a_provider_error_is_a_tool_message_and_the_agent_continues() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(403, text="secret"))
    integration, client, tool_name, args = _case("datadog", transport)
    model = ScriptedChatModel(
        responses=[tool_call_message(tool_name, args, "c1"), AIMessage("done")]
    )
    agent = make_agent(
        name="reader", model=model, tools=integration.tools(), context_schema=RunContext
    )

    result = await agent.ainvoke(
        {"messages": [HumanMessage("investigate")]}, context=RunContext(clients={"datadog": client})
    )

    message = next(item for item in result["messages"] if isinstance(item, ToolMessage))
    assert message.status == "error"
    assert "403" in str(message.content)
    assert "secret" not in str(message.content)
    assert len(model.calls) == 2


async def test_newrelic_entity_tool_accepts_a_continuation_cursor() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "actor": {
                        "entitySearch": {
                            "results": {
                                "entities": [{"guid": "entity-two"}],
                                "nextCursor": "third-page",
                            }
                        }
                    }
                }
            },
        )

    client = NewRelicClient(
        "https://example.test", "key", 42, transport=httpx.MockTransport(respond)
    )
    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "nr_entities", {"search": "name LIKE 'checkout'", "cursor": "second-page"}, "c1"
            ),
            AIMessage("done"),
        ]
    )
    agent = make_agent(
        name="reader", model=model, tools=NewRelicIntegration().tools(), context_schema=RunContext
    )

    result = await agent.ainvoke(
        {"messages": [HumanMessage("continue search")]},
        context=RunContext(clients={"newrelic": client}),
    )

    message = next(item for item in result["messages"] if isinstance(item, ToolMessage))
    assert message.status == "success"
    assert message.artifact == {"entities": [{"guid": "entity-two"}], "nextCursor": "third-page"}
    assert json.loads(seen[0].content)["variables"]["cursor"] == "second-page"
