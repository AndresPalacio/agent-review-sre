from __future__ import annotations

from pathlib import Path

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.domain.alerts.model import AlertSource
from fin_graph.graphs.investigation.graph import make_investigation_graph
from fin_graph.graphs.investigation.services import UnavailableServices
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.server.app import create_app
from fin_graph.server.manager import RunManager
from fin_graph.server.security import WebhookVerifier
from fin_graph.server.storage import Base, Storage


async def test_alertmanager_to_report_sse_dedupe_and_auth(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("operator-key", "operator")
    await storage.put_key("viewer-key", "viewer")
    await storage.put_key("admin-key", "admin")
    registry = IntegrationRegistry([], env_file=None)
    graph = make_investigation_graph(UnavailableServices(), registry, checkpointer=InMemorySaver())
    manager = RunManager(graph, storage, RunContext, max_concurrent=1)
    app = create_app(
        manager=manager,
        registry=registry,
        verifier=WebhookVerifier(
            tokens={
                "alertmanager": "webhook-key",
                "grafana": "grafana-key",
            }
        ),
    )
    alert = {
        "version": "4",
        "groupKey": "g",
        "status": "firing",
        "commonLabels": {"alertname": "HighCPU", "service": "checkout"},
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "HighCPU"},
                "startsAt": "2026-10-07T10:00:00Z",
            }
        ],
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/v1/threads")).status_code == 401
        assert (
            await client.post(
                "/v1/investigations", json={"text": "x"}, headers={"x-api-key": "viewer-key"}
            )
        ).status_code == 403
        assert (
            await client.post(
                "/v1/api-keys",
                json={"role": "viewer"},
                headers={"x-api-key": "operator-key"},
            )
        ).status_code == 403
        created_key = await client.post(
            "/v1/api-keys", json={"role": "viewer"}, headers={"x-api-key": "admin-key"}
        )
        assert created_key.status_code == 201
        assert (
            await client.get("/v1/threads", headers={"x-api-key": created_key.json()["key"]})
        ).status_code == 200
        assert (await client.post("/v1/alerts/alertmanager", json=alert)).status_code == 401
        assert (
            await client.post("/v1/alerts/alertmanager", content=b"not-json")
        ).status_code == 401
        assert (
            await client.post(
                "/v1/alerts/grafana",
                json=alert,
                headers={"x-sre-webhook-token": "grafana-key"},
            )
        ).status_code == 422
        headers = {"x-sre-webhook-token": "webhook-key"}
        response = await client.post("/v1/alerts/alertmanager", json=alert, headers=headers)
        assert response.status_code == 202, response.text
        run = response.json()
        await manager.wait(run["run_id"])
        report = await client.get(
            f"/v1/investigations/{run['thread_id']}", headers={"x-api-key": "viewer-key"}
        )
        assert report.status_code == 200
        assert report.json()["report"]["alert"]["title"] == "HighCPU"
        stream = await client.get(
            f"/v1/threads/{run['thread_id']}/stream", headers={"x-api-key": "viewer-key"}
        )
        assert "event: stage" in stream.text
        assert "event: status" in stream.text
        history = await client.get(
            f"/v1/threads/{run['thread_id']}/history", headers={"x-api-key": "viewer-key"}
        )
        last_id = history.json()[-1]["id"]
        replay = await client.get(
            f"/v1/threads/{run['thread_id']}/stream",
            headers={"x-api-key": "viewer-key", "Last-Event-ID": str(last_id - 1)},
        )
        assert f"id: {last_id}\n" in replay.text
        assert "event: stage" not in replay.text
        for index in range(501):
            await storage.add_event(run["run_id"], "bulk", {"index": index})
        bulk = await client.get(
            f"/v1/threads/{run['thread_id']}/stream",
            headers={"x-api-key": "viewer-key", "Last-Event-ID": str(last_id)},
        )
        assert bulk.text.count("event: bulk") == 501
        page = await client.get(
            f"/v1/threads/{run['thread_id']}/history?after_id={last_id}&limit=500",
            headers={"x-api-key": "viewer-key"},
        )
        assert len(page.json()) == 500
        tail = await client.get(
            f"/v1/threads/{run['thread_id']}/history?after_id={page.json()[-1]['id']}",
            headers={"x-api-key": "viewer-key"},
        )
        assert len(tail.json()) == 1
        assert (
            await client.post(
                "/v1/alerts/alertmanager",
                content=b"x" * (1024 * 1024 + 1),
                headers=headers,
            )
        ).status_code == 413

        async def oversized_chunks():
            yield b"x" * (512 * 1024)
            yield b"y" * (512 * 1024 + 1)

        assert (
            await client.post(
                "/v1/alerts/alertmanager", content=oversized_chunks(), headers=headers
            )
        ).status_code == 413
        assert (await client.post("/v1/alerts/alertmanager", json=alert, headers=headers)).json()[
            "thread_id"
        ] == run["thread_id"]
        assert (await client.get("/v1/ready")).status_code == 200
    await engine.dispose()


async def test_all_eight_webhook_routes_accept_authenticated_source_payloads(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sources.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("viewer", "viewer")
    registry = IntegrationRegistry([], env_file=None)
    manager = RunManager(
        make_investigation_graph(UnavailableServices(), registry, checkpointer=InMemorySaver()),
        storage,
        RunContext,
    )
    tokens = {source.value: f"token-{source.value}" for source in AlertSource}
    app = create_app(manager=manager, registry=registry, verifier=WebhookVerifier(tokens=tokens))
    payloads = {
        "alertmanager": {
            "version": "4",
            "groupKey": "am",
            "alerts": [{"status": "firing", "labels": {"alertname": "A"}}],
        },
        "grafana": {
            "orgId": 1,
            "title": "Grafana A",
            "alerts": [{"status": "firing", "labels": {"alertname": "A"}, "fingerprint": "g1"}],
        },
        "datadog": {
            "alert_transition": "Triggered",
            "alert_type": "error",
            "title": "Datadog A",
            "alert_id": "d1",
        },
        "cloudwatch": {
            "AlarmName": "CloudWatch A",
            "NewStateValue": "ALARM",
            "AlarmArn": "arn:aws:cloudwatch:us-east-1:1:alarm:a",
        },
        "sentry": {
            "action": "triggered",
            "data": {"event": {"event_id": "e1", "title": "Sentry A", "issue_id": "i1"}},
        },
        "pagerduty": {
            "event": {
                "event_type": "incident.triggered",
                "data": {"id": "p1", "title": "PagerDuty A"},
            }
        },
        "newrelic": {
            "id": "n1",
            "issueUrl": "https://one.newrelic.com/a",
            "title": "NewRelic A",
            "state": "ACTIVATED",
        },
        "generic": {"title": "Generic A"},
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        for source, payload in payloads.items():
            response = await client.post(
                f"/v1/alerts/{source}",
                json=payload,
                headers={"x-sre-webhook-token": tokens[source]},
            )
            assert response.status_code == 202, (source, response.text)
            run = response.json()
            await manager.wait(run["run_id"])
            report = await client.get(
                f"/v1/investigations/{run['thread_id']}", headers={"x-api-key": "viewer"}
            )
            assert report.status_code == 200
            assert report.json()["report"]["alert"]["source"] == source
    await engine.dispose()
