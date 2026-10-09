from __future__ import annotations

import httpx
import pytest

from sre_graph.domain.tools import Domain, read_tool_meta
from sre_graph.integrations.base import IntegrationError, VerifyStatus
from sre_graph.integrations.tempo.client import TempoClient
from sre_graph.integrations.tempo.integration import TempoIntegration
from sre_graph.integrations.tempo.settings import TempoSettings


def test_tempo_is_disabled_until_configured() -> None:
    assert TempoIntegration().load_settings(None) is None
    with pytest.raises(ValueError, match="url"):
        TempoSettings(_env_file=None, enabled=True)


async def test_tempo_search_and_trace_use_native_routes() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"traces": []})

    client = TempoClient("https://tempo.example", transport=httpx.MockTransport(respond))
    await client.search('{resource.service.name="checkout"}', 100, 200, 20)
    await client.trace("a" * 32)

    assert [r.url.path for r in seen] == ["/api/search", "/api/v2/traces/" + "a" * 32]
    assert seen[0].url.params["q"] == '{resource.service.name="checkout"}'
    assert seen[0].url.params["limit"] == "20"
    assert seen[1].headers["Accept"] == "application/json"
    assert (await TempoIntegration().verify(client)).status is VerifyStatus.OK


async def test_tempo_trace_id_is_validated_before_request() -> None:
    client = TempoClient("https://tempo.example")
    with pytest.raises(IntegrationError, match="trace id"):
        await client.trace("../admin")


def test_tempo_tools_route_to_traces() -> None:
    assert [t.name for t in TempoIntegration().tools()] == ["tempo_search", "tempo_trace"]
    assert all(Domain.TRACES in read_tool_meta(t).domains for t in TempoIntegration().tools())
