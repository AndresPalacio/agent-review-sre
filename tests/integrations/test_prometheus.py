from __future__ import annotations

import httpx
import pytest

from fin_graph.domain.tools import Domain, SideEffectLevel, read_tool_meta
from fin_graph.integrations.base import IntegrationError, VerifyStatus
from fin_graph.integrations.catalog import default_registry
from fin_graph.integrations.prometheus.client import PrometheusClient
from fin_graph.integrations.prometheus.integration import MimirIntegration, PrometheusIntegration
from fin_graph.integrations.prometheus.settings import MimirSettings, PrometheusSettings


@pytest.fixture(autouse=True)
def _observability_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests cover the SRE catalog; switch off the market data that is on by default."""
    monkeypatch.setenv("YAHOO_ENABLED", "false")


def _transport(seen: list[httpx.Request], *, status: int = 200) -> httpx.MockTransport:
    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            status, json={"status": "success", "data": {"resultType": "vector", "result": []}}
        )

    return httpx.MockTransport(respond)


def test_prometheus_and_mimir_are_off_by_default() -> None:
    assert PrometheusIntegration().load_settings(None) is None
    assert MimirIntegration().load_settings(None) is None


def test_only_enabled_observability_endpoints_register_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROMETHEUS_ENABLED", "true")
    monkeypatch.setenv("PROMETHEUS_URL", "https://prom.example")
    registry = default_registry(env_file=None)

    context = registry.run_context()

    assert list(context.clients) == ["prometheus"]
    assert [tool.name for tool in registry.tools(context)] == [
        "prom_query",
        "prom_query_range",
        "prom_labels",
        "prom_series",
        "prom_alerts",
        "prom_rules",
        "prom_targets",
    ]


def test_enabled_prometheus_requires_a_url() -> None:
    with pytest.raises(ValueError, match="url"):
        PrometheusSettings(_env_file=None, enabled=True)


async def test_prometheus_query_and_range_use_documented_parameters() -> None:
    seen: list[httpx.Request] = []
    client = PrometheusClient("https://prom.example", transport=_transport(seen))

    assert (await client.query("up"))["resultType"] == "vector"
    await client.query_range("rate(http_requests_total[5m])", 100, 200, 15)

    assert [request.url.path for request in seen] == ["/api/v1/query", "/api/v1/query_range"]
    assert seen[0].url.params["query"] == "up"
    assert seen[1].url.params["start"] == "100"
    assert seen[1].url.params["end"] == "200"
    assert seen[1].url.params["step"] == "15"


async def test_prometheus_labels_series_alerts_rules_and_targets() -> None:
    seen: list[httpx.Request] = []
    client = PrometheusClient("https://prom.example", transport=_transport(seen))

    await client.labels(100, 200)
    await client.series('{job="api"}', 100, 200)
    await client.alerts()
    await client.rules()
    await client.targets()

    assert [request.url.path for request in seen] == [
        "/api/v1/labels",
        "/api/v1/series",
        "/api/v1/alerts",
        "/api/v1/rules",
        "/api/v1/targets",
    ]
    assert seen[1].url.params["match[]"] == '{job="api"}'


async def test_prometheus_rejects_error_envelope() -> None:
    client = PrometheusClient(
        "https://prom.example",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "error", "error": "bad PromQL"})
        ),
    )
    with pytest.raises(IntegrationError, match="Prometheus"):
        await client.query("bad")


async def test_query_range_rejects_invalid_window() -> None:
    client = PrometheusClient("https://prom.example", transport=_transport([]))
    with pytest.raises(IntegrationError, match="window"):
        await client.query_range("up", 200, 100, 15)
    with pytest.raises(IntegrationError, match="step"):
        await client.query_range("up", 100, 200, 0)


async def test_mimir_passes_tenant_header() -> None:
    seen: list[httpx.Request] = []
    settings = MimirSettings(
        _env_file=None, enabled=True, url="https://mimir.example/prometheus", tenant="team-a"
    )
    client = MimirIntegration().build_client(settings)
    client.http.transport = _transport(seen)

    await client.query("up")

    assert seen[0].url.path == "/prometheus/api/v1/query"
    assert seen[0].headers["X-Scope-OrgID"] == "team-a"


async def test_verify_requires_read_access() -> None:
    seen: list[httpx.Request] = []
    client = PrometheusClient("https://prom.example", transport=_transport(seen, status=403))
    with pytest.raises(IntegrationError, match="403"):
        await PrometheusIntegration().verify(client)


def test_prometheus_tools_are_read_only_and_routed() -> None:
    names = [tool.name for tool in PrometheusIntegration().tools()]
    assert names == [
        "prom_query",
        "prom_query_range",
        "prom_labels",
        "prom_series",
        "prom_alerts",
        "prom_rules",
        "prom_targets",
    ]
    for tool in MimirIntegration().tools():
        meta = read_tool_meta(tool)
        assert meta is not None
        assert meta.integration == "mimir"
        assert Domain.METRICS in meta.domains
        assert meta.side_effect_level is SideEffectLevel.READ_ONLY


async def test_prometheus_verify_reads_query() -> None:
    client = PrometheusClient("https://prom.example", transport=_transport([]))
    assert (await PrometheusIntegration().verify(client)).status is VerifyStatus.OK
