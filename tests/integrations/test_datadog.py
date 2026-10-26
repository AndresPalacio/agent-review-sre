from __future__ import annotations

import json

import httpx
import pytest

from fin_graph.domain.tools import Domain, SideEffectLevel, read_tool_meta
from fin_graph.integrations.base import IntegrationError, VerifyStatus
from fin_graph.integrations.datadog.client import DatadogClient
from fin_graph.integrations.datadog.integration import DatadogIntegration
from fin_graph.integrations.datadog.settings import DatadogSettings


def test_datadog_is_disabled_and_requires_both_keys() -> None:
    assert DatadogIntegration().load_settings(None) is None
    with pytest.raises(ValueError, match="api_key"):
        DatadogSettings(_env_file=None, enabled=True)
    with pytest.raises(ValueError, match="app_key"):
        DatadogSettings(_env_file=None, enabled=True, api_key="a")


async def test_datadog_logs_preserve_next_cursor_and_key_headers() -> None:
    seen: list[httpx.Request] = []
    payload = {"data": [{"id": "l1"}], "meta": {"page": {"after": "next-1"}}}

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    client = DatadogClient(
        "https://api.datadoghq.com",
        "api-secret",
        "app-secret",
        transport=httpx.MockTransport(respond),
    )
    result = await client.logs("service:checkout", 100, 200, 25)

    assert result == payload
    assert seen[0].url.path == "/api/v2/logs/events/search"
    body = json.loads(seen[0].content)
    assert body["filter"] == {"query": "service:checkout", "from": "100000", "to": "200000"}
    assert body["page"]["limit"] == 25
    assert seen[0].headers["DD-API-KEY"] == "api-secret"
    assert seen[0].headers["DD-APPLICATION-KEY"] == "app-secret"


async def test_datadog_spans_use_v2_search_shape() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [], "meta": {}})

    client = DatadogClient(
        "https://api.datadoghq.com", "a", "b", transport=httpx.MockTransport(respond)
    )
    await client.spans("service:checkout", 100, 200, 10)

    assert seen[0].url.path == "/api/v2/spans/events/search"
    body = json.loads(seen[0].content)
    assert body["data"]["type"] == "search_request"
    assert body["data"]["attributes"]["filter"]["query"] == "service:checkout"


async def test_datadog_other_read_routes_and_verify() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [], "status": "ok", "series": []})

    client = DatadogClient(
        "https://api.datadoghq.com", "a", "b", transport=httpx.MockTransport(respond)
    )
    await client.metrics("avg:system.cpu.user{*}", 100, 200)
    await client.monitors(10)
    await client.events("service:checkout", 100, 200, 20)
    await client.incidents(10)
    assert (await DatadogIntegration().verify(client)).status is VerifyStatus.OK
    assert [r.url.path for r in seen] == [
        "/api/v1/query",
        "/api/v1/monitor",
        "/api/v2/events",
        "/api/v2/incidents",
        "/api/v1/monitor",
    ]


async def test_datadog_logs_have_bounded_window() -> None:
    client = DatadogClient("https://api.datadoghq.com", "a", "b")
    with pytest.raises(IntegrationError, match="window"):
        await client.logs("*", 200, 100)


async def test_datadog_permission_error_has_no_keys() -> None:
    client = DatadogClient(
        "https://api.datadoghq.com",
        "api-secret",
        "app-secret",
        transport=httpx.MockTransport(lambda r: httpx.Response(403, text="app-secret")),
    )
    with pytest.raises(IntegrationError) as caught:
        await client.monitors()
    assert "403" in str(caught.value)
    assert "app-secret" not in str(caught.value)


def test_datadog_tools_are_six_readers_with_metadata() -> None:
    tools = DatadogIntegration().tools()
    assert [tool.name for tool in tools] == [
        "dd_logs",
        "dd_metrics",
        "dd_monitors",
        "dd_events",
        "dd_spans",
        "dd_incidents",
    ]
    assert Domain.LOGS in read_tool_meta(tools[0]).domains
    assert Domain.TRACES in read_tool_meta(tools[4]).domains
    assert all(
        read_tool_meta(tool).side_effect_level is SideEffectLevel.READ_ONLY for tool in tools
    )
