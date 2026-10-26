from __future__ import annotations

import httpx
import pytest

from fin_graph.domain.tools import Domain, read_tool_meta
from fin_graph.integrations.base import IntegrationError, VerifyStatus
from fin_graph.integrations.loki.client import LokiClient
from fin_graph.integrations.loki.integration import LokiIntegration
from fin_graph.integrations.loki.settings import LokiSettings


def test_loki_is_disabled_until_configured() -> None:
    assert LokiIntegration().load_settings(None) is None
    with pytest.raises(ValueError, match="url"):
        LokiSettings(_env_file=None, enabled=True)


async def test_loki_range_uses_nanoseconds_and_tenant() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "streams", "result": []}}
        )

    client = LokiClient(
        "https://loki.example", tenant="team-a", transport=httpx.MockTransport(respond)
    )
    await client.query_range('{app="checkout"}', 100, 200, 50)
    await client.labels(100, 200)

    assert [r.url.path for r in seen] == ["/loki/api/v1/query_range", "/loki/api/v1/labels"]
    assert seen[0].url.params["start"] == "100000000000"
    assert seen[0].url.params["end"] == "200000000000"
    assert seen[0].headers["X-Scope-OrgID"] == "team-a"
    assert (await LokiIntegration().verify(client)).status is VerifyStatus.OK


async def test_loki_rejects_invalid_window() -> None:
    client = LokiClient("https://loki.example")
    with pytest.raises(IntegrationError, match="window"):
        await client.query_range("{}", 200, 100, 10)


def test_loki_tools_route_to_logs() -> None:
    assert [t.name for t in LokiIntegration().tools()] == ["loki_query_range", "loki_labels"]
    assert all(Domain.LOGS in read_tool_meta(t).domains for t in LokiIntegration().tools())
