from __future__ import annotations

import httpx
import pytest

from sre_graph.domain.tools import SideEffectLevel, read_tool_meta
from sre_graph.integrations.alertmanager.client import AlertmanagerClient
from sre_graph.integrations.alertmanager.integration import AlertmanagerIntegration
from sre_graph.integrations.alertmanager.settings import AlertmanagerSettings
from sre_graph.integrations.base import IntegrationError, VerifyStatus


def test_alertmanager_is_disabled_and_requires_url_when_enabled() -> None:
    assert AlertmanagerIntegration().load_settings(None) is None
    with pytest.raises(ValueError, match="url"):
        AlertmanagerSettings(_env_file=None, enabled=True)


async def test_alerts_and_silences_are_read_only() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"id": "a1"}])

    client = AlertmanagerClient("https://alerts.example", transport=httpx.MockTransport(respond))
    assert await client.alerts() == [{"id": "a1"}]
    assert await client.silences() == [{"id": "a1"}]
    assert [r.url.path for r in seen] == ["/api/v2/alerts", "/api/v2/silences"]
    assert [r.method for r in seen] == ["GET", "GET"]
    assert (await AlertmanagerIntegration().verify(client)).status is VerifyStatus.OK


async def test_alertmanager_verify_requires_read_access() -> None:
    client = AlertmanagerClient(
        "https://alerts.example",
        transport=httpx.MockTransport(lambda request: httpx.Response(403, text="denied")),
    )
    with pytest.raises(IntegrationError, match="403"):
        await AlertmanagerIntegration().verify(client)


def test_alertmanager_tools_are_read_only() -> None:
    assert [tool.name for tool in AlertmanagerIntegration().tools()] == ["am_alerts", "am_silences"]
    assert all(
        read_tool_meta(tool).side_effect_level is SideEffectLevel.READ_ONLY
        for tool in AlertmanagerIntegration().tools()
    )
