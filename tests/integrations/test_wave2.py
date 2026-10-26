from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from fin_graph.domain.tools import Domain, SideEffectLevel, read_tool_meta
from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.catalog import default_registry
from fin_graph.integrations.wave2 import wave2_integrations
from fin_graph.integrations.wave2.client import Wave2Client
from fin_graph.integrations.wave2.settings import WAVE2_SETTINGS

IDS = {
    "opsgenie",
    "incident_io",
    "jira",
    "honeycomb",
    "elasticsearch",
    "opensearch",
    "splunk",
    "victoria_logs",
    "coralogix",
    "azure",
    "argocd",
    "helm",
    "gitlab",
}


def settings(identity: str, **kwargs: Any) -> Any:
    fields = dict(enabled=True, url="https://provider.test", token="private-token")
    if identity == "honeycomb":
        fields["dataset"] = "app"
    if identity == "jira":
        fields["email"] = "operator@example.test"
    if identity == "azure":
        fields["workspace_id"] = "12345678-1234-1234-1234-123456789abc"
    if identity in ("splunk", "elasticsearch", "opensearch"):
        fields["index"] = "logs"
    if identity == "gitlab":
        fields["project"] = "team/app"
    fields.update(kwargs)
    return WAVE2_SETTINGS[identity](_env_file=None, **fields)


def test_all_named_integrations_are_registered_and_safe() -> None:
    assert set(default_registry(env_file=None).ids) >= IDS
    assert {item.id for item in wave2_integrations()} == IDS
    for item in wave2_integrations():
        assert item.tools()
        for tool in item.tools():
            meta = read_tool_meta(tool)
            assert meta and meta.integration == item.id and meta.domains
            assert meta.side_effect_level == SideEffectLevel.READ_ONLY
            assert "runtime" not in tool.tool_call_schema.model_fields
    honeycomb = next(item for item in wave2_integrations() if item.id == "honeycomb")
    assert all(not read_tool_meta(tool).idempotent for tool in honeycomb.tools())


@pytest.mark.parametrize(
    ("identity", "operation", "args", "path", "response"),
    [
        ("opsgenie", "alerts", {"limit": 1}, "/v2/alerts", {"data": [{"id": "a"}]}),
        (
            "incident_io",
            "incidents",
            {"limit": 1},
            "/v2/incidents",
            {"incidents": [{"id": "i"}], "pagination_meta": {"after": "next"}},
        ),
        (
            "jira",
            "issues",
            {"query": "project = APP", "limit": 1},
            "/rest/api/3/search/jql",
            {"issues": [{"key": "APP-1"}], "nextPageToken": "next"},
        ),
        (
            "elasticsearch",
            "logs",
            {"query": "error", "start": 100, "end": 200},
            "/logs/_search",
            {"hits": {"hits": [{"_source": {"message": "oops"}}], "total": {"value": 1}}},
        ),
        (
            "opensearch",
            "logs",
            {"query": "error", "start": 100, "end": 200},
            "/logs/_search",
            {"hits": {"hits": [{"_source": {"message": "oops"}}]}},
        ),
        (
            "azure",
            "logs",
            {"query": "AppTraces", "start": 100, "end": 200},
            "/v1/workspaces/12345678-1234-1234-1234-123456789abc/query",
            {"tables": [{"columns": [{"name": "message"}], "rows": [["oops"]]}]},
        ),
        (
            "argocd",
            "applications",
            {"limit": 1},
            "/api/v1/applications",
            {"items": [{"metadata": {"name": "app"}}]},
        ),
        (
            "gitlab",
            "commits",
            {"limit": 1},
            "/api/v4/projects/team%2Fapp/repository/commits",
            [{"id": "abc"}],
        ),
    ],
)
async def test_provider_reads_fixed_paths(
    identity: str, operation: str, args: dict[str, Any], path: str, response: Any
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.raw_path.decode().split("?", 1)[0] == path
        assert "private-token" not in str(request.url)
        return httpx.Response(200, json=response)

    client = Wave2Client(identity, settings(identity), transport=httpx.MockTransport(handler))
    result = await client.call(operation, **args)
    assert result["items"] and isinstance(result["truncated"], bool)
    assert len(requests) == 1
    if identity == "jira":
        assert requests[0].method == "POST"
    if identity in {"elasticsearch", "opensearch"}:
        body = json.loads(requests[0].content)
        assert body["query"]["bool"]["filter"][0]["range"]["@timestamp"]["gte"] == 100000


@pytest.mark.parametrize("identity", ["splunk", "victoria_logs", "coralogix"])
async def test_ndjson_queries_bound_window_and_rows(identity: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if identity == "splunk":
            form = parse_qs(request.content.decode())
            assert form["search"] == ['search index="logs" "error | collect index=evil" | head 1']
            assert form["earliest_time"] == ["100"]
            return httpx.Response(
                200, text='{"result":{"message":"oops"}}\n{"result":{"message":"another"}}\n'
            )
        if identity == "victoria_logs":
            form = parse_qs(request.content.decode())
            assert form["start"] == ["100"] and form["end"] == ["200"]
            return httpx.Response(200, text='{"_msg":"oops"}\n{"_msg":"another"}\n')
        body = json.loads(request.content)
        assert body["metadata"]["limit"] == 1
        return httpx.Response(
            200, text='{"result":{"results":[{"userData":"{\\"message\\":\\"oops\\"}"}]}}\n'
        )

    client = Wave2Client(identity, settings(identity), transport=httpx.MockTransport(handler))
    result = await client.call(
        "logs", query="error | collect index=evil", start=100, end=200, limit=1
    )
    assert len(result["items"]) == 1
    assert len(seen) == 1


async def test_honeycomb_creates_once_and_polls_bounded() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/1/queries/app":
            body = json.loads(request.content)
            assert body["start_time"] == 100 and body["end_time"] == 200 and body["limit"] == 1
            return httpx.Response(200, json={"id": "query"})
        if request.url.path == "/1/query_results/app":
            return httpx.Response(200, json={"id": "result", "complete": False})
        return httpx.Response(
            200,
            json={"id": "result", "complete": True, "data": {"results": [{"data": {"COUNT": 1}}]}},
        )

    client = Wave2Client(
        "honeycomb",
        settings("honeycomb", poll_interval_seconds=0),
        transport=httpx.MockTransport(handler),
    )
    result = await client.call("traces", service="api", start=100, end=200, limit=1)
    assert result["items"] == [{"COUNT": 1}]
    assert paths == ["/1/queries/app", "/1/query_results/app", "/1/query_results/app/result"]


@pytest.mark.parametrize("identity", sorted(IDS - {"helm"}))
async def test_failure_never_echoes_provider_or_credentials(identity: str) -> None:
    client = Wave2Client(
        identity,
        settings(identity),
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text="private-token")),
    )
    with pytest.raises(IntegrationError) as caught:
        await client.verify()
    assert "private-token" not in str(caught.value)


@pytest.mark.parametrize(
    "identity", ["splunk", "victoria_logs", "coralogix", "elasticsearch", "opensearch", "azure"]
)
async def test_invalid_window_and_limit_fail_before_io(identity: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid input made a request")

    client = Wave2Client(identity, settings(identity), transport=httpx.MockTransport(handler))
    for kwargs in (
        {"start": 200, "end": 100},
        {"start": 1, "end": 1000000},
        {"start": 1, "end": 2, "limit": 101},
    ):
        with pytest.raises((IntegrationError, ValidationError)):
            await client.call("logs", query="error", **kwargs)


@pytest.mark.parametrize("bad", ["../x", "x?token=y", "x#y", "x/y", "--help", "x%2Fy"])
async def test_path_id_injection_is_rejected(bad: str) -> None:
    client = Wave2Client(
        "opsgenie",
        settings("opsgenie"),
        transport=httpx.MockTransport(lambda request: pytest.fail("unsafe ID requested")),
    )
    with pytest.raises((IntegrationError, ValidationError)):
        await client.call("alert", identifier=bad)


async def test_ndjson_malformed_and_oversize_are_errors() -> None:
    for body in ("not json\n", '"scalar"\n', "x" * (2 * 1024 * 1024 + 1)):
        client = Wave2Client(
            "victoria_logs",
            settings("victoria_logs"),
            transport=httpx.MockTransport(
                lambda request, body=body: httpx.Response(200, text=body)
            ),
        )
        with pytest.raises(IntegrationError):
            await client.call("logs", query="error", start=1, end=2)


def test_settings_reject_incomplete_or_unsafe_configuration() -> None:
    for identity in IDS - {"helm"}:
        with pytest.raises(ValidationError):
            WAVE2_SETTINGS[identity](_env_file=None, enabled=True)
    for url in ("https://user:secret@host", "https://host/?token=secret", "ftp://host"):
        with pytest.raises(ValidationError):
            settings("opsgenie", url=url)


def test_registry_domains_include_wave2_logs() -> None:
    from fin_graph.integrations.context import RunContext

    registry = default_registry(env_file=None)
    context = RunContext(clients={"splunk": object(), "argocd": object()})
    tools = registry.tools(context, domains={Domain.LOGS})
    assert tools and all(read_tool_meta(tool).integration == "splunk" for tool in tools)
