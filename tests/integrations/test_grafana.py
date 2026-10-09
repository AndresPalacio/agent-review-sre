from __future__ import annotations

import httpx
import pytest

from sre_graph.domain.tools import SideEffectLevel, read_tool_meta
from sre_graph.integrations.base import IntegrationError, VerifyStatus
from sre_graph.integrations.grafana.client import GrafanaClient
from sre_graph.integrations.grafana.integration import GrafanaIntegration
from sre_graph.integrations.grafana.settings import GrafanaSettings


def test_grafana_is_disabled_until_configured() -> None:
    assert GrafanaIntegration().load_settings(None) is None
    with pytest.raises(ValueError, match="url"):
        GrafanaSettings(_env_file=None, enabled=True)


async def test_grafana_reads_rules_annotations_and_dashboards() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"uid": "a1"}])

    client = GrafanaClient(
        "https://grafana.example", token="token", transport=httpx.MockTransport(respond)
    )
    assert await client.alert_rules() == [{"uid": "a1"}]
    assert await client.annotations(100, 200) == [{"uid": "a1"}]
    assert await client.dashboards("checkout") == [{"uid": "a1"}]
    assert [r.url.path for r in seen] == [
        "/api/v1/provisioning/alert-rules",
        "/api/annotations",
        "/api/search",
    ]
    assert seen[1].url.params["from"] == "100000"
    assert seen[2].url.params["query"] == "checkout"
    assert seen[0].headers["Authorization"] == "Bearer token"
    assert (await GrafanaIntegration().verify(client)).status is VerifyStatus.OK


async def test_grafana_verify_fails_without_read_access() -> None:
    client = GrafanaClient(
        "https://grafana.example", transport=httpx.MockTransport(lambda r: httpx.Response(403))
    )
    with pytest.raises(IntegrationError, match="403"):
        await GrafanaIntegration().verify(client)


def test_grafana_tools_are_read_only() -> None:
    assert [t.name for t in GrafanaIntegration().tools()] == [
        "grafana_alert_rules",
        "grafana_annotations",
        "grafana_dashboards",
    ]
    assert all(
        read_tool_meta(t).side_effect_level is SideEffectLevel.READ_ONLY
        for t in GrafanaIntegration().tools()
    )
