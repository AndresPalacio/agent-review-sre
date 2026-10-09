from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from tests.integrations.test_wave2 import settings

from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.wave2 import wave2_integrations
from sre_graph.integrations.wave2.client import Wave2Client, sanitize


@pytest.mark.parametrize(
    ("identity", "operation", "args", "path", "payload"),
    [
        ("opsgenie", "alert", {"identifier": "a"}, "/v2/alerts/a", {"data": {"id": "a"}}),
        (
            "opsgenie",
            "alert_logs",
            {"identifier": "a"},
            "/v2/alerts/a/logs",
            {"data": [{"log": "ack"}]},
        ),
        (
            "incident_io",
            "incident",
            {"identifier": "i"},
            "/v2/incidents/i",
            {"incident": {"id": "i"}},
        ),
        (
            "incident_io",
            "updates",
            {"identifier": "i"},
            "/v2/incident_updates",
            {"incident_updates": [{"message": "update"}]},
        ),
        ("jira", "issue", {"identifier": "APP-1"}, "/rest/api/3/issue/APP-1", {"key": "APP-1"}),
        ("elasticsearch", "health", {}, "/_cluster/health", {"status": "green"}),
        ("opensearch", "health", {}, "/_cluster/health", {"status": "yellow"}),
        (
            "argocd",
            "application",
            {"identifier": "app"},
            "/api/v1/applications/app",
            {"metadata": {"name": "app"}, "status": {"sync": {"status": "Synced"}}},
        ),
        (
            "argocd",
            "diff",
            {"identifier": "app"},
            "/api/v1/applications/app/managed-resources",
            {
                "items": [
                    {
                        "kind": "Deployment",
                        "liveState": '{"kind":"Deployment","spec":{"replicas":1}}',
                        "targetState": '{"kind":"Deployment","spec":{"replicas":2}}',
                    }
                ]
            },
        ),
        (
            "gitlab",
            "merge_requests",
            {},
            "/api/v4/projects/team%2Fapp/merge_requests",
            [{"iid": 1}],
        ),
        ("gitlab", "pipelines", {}, "/api/v4/projects/team%2Fapp/pipelines", [{"id": 1}]),
        (
            "gitlab",
            "file",
            {"path": "config/app.yml", "ref": "release/v1"},
            "/api/v4/projects/team%2Fapp/repository/files/config%2Fapp.yml",
            {"encoding": "base64", "content": base64.b64encode(b"replicas: 2").decode()},
        ),
    ],
)
async def test_other_finite_operations(
    identity: str, operation: str, args: dict[str, Any], path: str, payload: Any
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.raw_path.decode().split("?", 1)[0] == path
        if operation == "updates":
            assert request.url.params["incident_id"] == "i"
        if operation == "file":
            assert request.url.params["ref"] == "release/v1"
        return httpx.Response(200, json=payload)

    client = Wave2Client(identity, settings(identity), transport=httpx.MockTransport(handler))
    result = await client.call(operation, **args)
    assert result["items"]


async def test_typed_tool_runtime_injected_and_artifact_collectable() -> None:
    client = Wave2Client(
        "opsgenie",
        settings("opsgenie"),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": [{"id": "a"}]})
        ),
    )
    tools = next(item for item in wave2_integrations() if item.id == "opsgenie").tools()
    graph = StateGraph(MessagesState, context_schema=RunContext)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    result = await graph.compile().ainvoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "opsgenie_alerts", "args": {"limit": 1}, "id": "call"}],
                )
            ]
        },
        context=RunContext(clients={"opsgenie": client}),
    )
    message = result["messages"][-1]
    assert message.status == "success"
    assert message.artifact["items"] == [{"id": "a"}]


@pytest.mark.parametrize(
    "identity", ["splunk", "victoria_logs", "coralogix", "azure", "elasticsearch", "opensearch"]
)
async def test_total_deadline_applies_to_hanging_transport(identity: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    client = Wave2Client(
        identity,
        settings(identity, request_timeout_seconds=0.01),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(IntegrationError, match=r"deadline|TimeoutError"):
        await client.call("logs", query="error", start=100, end=200)


async def test_honeycomb_poll_budget_no_retry_creations() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json={"id": "job", "complete": False})

    client = Wave2Client(
        "honeycomb",
        settings("honeycomb", poll_attempts=2, poll_interval_seconds=0),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(IntegrationError, match="poll budget"):
        await client.call("traces", service="api", start=1, end=2)
    assert methods == ["POST", "POST", "GET", "GET"]


@pytest.mark.parametrize(
    "payload",
    [
        {"result": {"results": [{"userData": "not-json"}]}},
        {"error": {"message": "private-token"}},
        {"unrecognized": []},
    ],
)
async def test_coralogix_bad_stream_is_not_empty_success(payload: Any) -> None:
    client = Wave2Client(
        "coralogix",
        settings("coralogix"),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=json.dumps(payload))
        ),
    )
    with pytest.raises(IntegrationError) as caught:
        await client.call("logs", query="", start=1, end=2)
    assert "private-token" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tables": [{"columns": [{}], "rows": [[1]]}]},
        {"tables": [{"columns": [{"name": "x"}], "rows": [[1, 2]]}]},
        {"error": {"code": "PartialError"}},
    ],
)
async def test_azure_malformed_or_partial_data_fails(payload: Any) -> None:
    client = Wave2Client(
        "azure",
        settings("azure"),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    with pytest.raises(IntegrationError):
        await client.call("logs", start=1, end=2)


def test_secret_resources_and_named_secret_values_are_scrubbed() -> None:
    secret = {
        "kind": "Secret",
        "metadata": {
            "name": "app",
            "annotations": {
                "kubectl.kubernetes.io/last-applied-configuration": (
                    '{"data":{"arbitrary":"private"}}'
                )
            },
        },
        "data": {"arbitrary": "private"},
    }
    result = sanitize(
        {
            "targetState": json.dumps(secret),
            "env": [{"name": "DB_PASSWORD", "value": "private"}],
            "values": "arbitrary: private",
        }
    )
    assert "private" not in json.dumps(result)


def test_last_applied_annotations_of_non_secret_resources_are_sanitized() -> None:
    annotation = json.dumps(
        {"kind": "Deployment", "env": [{"name": "DB_PASSWORD", "value": "private"}]}
    )
    result = sanitize(
        {
            "metadata": {
                "annotations": {"kubectl.kubernetes.io/last-applied-configuration": annotation}
            }
        }
    )
    assert "private" not in json.dumps(result)
