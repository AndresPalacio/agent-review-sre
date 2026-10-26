from __future__ import annotations

import json

import httpx

from fin_graph.delivery.client import PagerDutyClient
from fin_graph.integrations.alertmanager.client import AlertmanagerClient
from fin_graph.integrations.datadog.client import DatadogClient
from fin_graph.integrations.github.client import GitHubClient
from fin_graph.integrations.grafana.client import GrafanaClient
from fin_graph.integrations.sentry.client import SentryClient


async def test_vendor_mutations_use_expected_endpoints_offline() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/api/v2/silences" and request.method == "POST":
            return httpx.Response(200, json={"silenceID": "s1"})
        if path == "/api/v2/silence/s1" and request.method == "DELETE":
            return httpx.Response(200, json={})
        if path == "/api/annotations":
            return httpx.Response(200, json={"id": 4})
        if path == "/api/v1/monitor/42/mute":
            return httpx.Response(200, json={"id": 42})
        if path == "/api/0/organizations/acme/issues/99/":
            return httpx.Response(200, json={"id": "99", "status": "resolved"})
        if path == "/repos/acme/web/issues":
            return httpx.Response(201, json={"number": 8})
        if path == "/incidents/P123":
            return httpx.Response(200, json={"incident": {"id": "P123", "status": "acknowledged"}})
        raise AssertionError(f"unexpected {request.method} {path}")

    transport = httpx.MockTransport(respond)
    alertmanager = AlertmanagerClient("https://am.example", transport=transport)
    grafana = GrafanaClient("https://grafana.example", transport=transport)
    datadog = DatadogClient("https://dd.example", "api", "app", transport=transport)
    sentry = SentryClient("https://sentry.example", "acme", "token", transport=transport)
    github = GitHubClient("https://github.example", "acme", "web", "token", transport=transport)
    pagerduty = PagerDutyClient("token", "oncall@example.org", transport=transport)

    assert (
        await alertmanager.create_silence(
            [{"name": "alertname", "value": "HighCPU", "isRegex": False}],
            "2026-10-08T00:00:00Z",
            "2026-10-08T01:00:00Z",
            "oncall",
            "incident",
        )
        == "s1"
    )
    await alertmanager.expire_silence("s1")
    assert await grafana.create_annotation("Incident investigated", 1791417600000, ["sre"]) == 4
    await datadog.mute_monitor(42, 1791421200)
    assert (await sentry.update_issue_status("99", "resolved"))["status"] == "resolved"
    assert await github.create_issue("RCA follow-up", "Evidence E1") == 8
    assert await pagerduty.update_incident_status("P123", "acknowledged") == "acknowledged"
    assert json.loads(seen[0].content)["matchers"][0]["name"] == "alertname"
    assert seen[1].method == "DELETE"
    assert seen[-1].headers["From"] == "oncall@example.org"
