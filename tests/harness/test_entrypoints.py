"""Exercise product entry points with local storage and provider-free services."""

from __future__ import annotations

import json

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner

from fin_graph.graphs.investigation.services import UnavailableServices
from fin_graph.harness.runtime import make_investigation_graph
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.scheduler.jobs import HealthJobs
from fin_graph.server.app import create_app
from fin_graph.server.manager import RunManager
from fin_graph.server.security import WebhookVerifier
from fin_graph.server.storage import Base, Storage


@pytest.mark.parametrize("source", ["api", "webhook", "scheduler"])
async def test_api_webhook_and_scheduled_incidents_expose_same_runtime(tmp_path, source):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'entrypoints.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("operator", "operator")
    registry = IntegrationRegistry([], env_file=None)
    graph = make_investigation_graph(UnavailableServices(), registry, checkpointer=InMemorySaver())
    manager = RunManager(graph, storage, RunContext)
    app = create_app(
        manager=manager,
        registry=registry,
        verifier=WebhookVerifier(tokens={"alertmanager": "webhook-key"}),
    )
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            if source == "api":
                response = await client.post(
                    "/v1/investigations",
                    json={"alert": {"title": "API error"}},
                    headers={"x-api-key": "operator"},
                )
                assert response.status_code == 202
                run = response.json()
            elif source == "webhook":
                response = await client.post(
                    "/v1/alerts/alertmanager",
                    headers={"x-sre-webhook-token": "webhook-key"},
                    json={
                        "version": "4",
                        "status": "firing",
                        "groupKey": "g",
                        "commonLabels": {"alertname": "API error"},
                        "alerts": [{"status": "firing", "labels": {"alertname": "API error"}}],
                    },
                )
                assert response.status_code == 202, response.text
                run = response.json()
            else:

                class Probe:
                    async def query(self, expression):
                        return {"resultType": "scalar", "result": [0, "0"]}

                jobs = HealthJobs(
                    storage, manager, lambda: RunContext(clients={"prometheus": Probe()})
                )
                await jobs.health_check(
                    "cluster",
                    {
                        "kind": "health_check",
                        "interval_minutes": 5,
                        "checks": [
                            {"id": "up", "expression": "up", "operator": "lt", "threshold": 1}
                        ],
                    },
                )
                run = (await storage.list_runs(10))[0]
            await manager.wait(run["run_id"])
            result = await client.get(
                f"/v1/investigations/{run['thread_id']}", headers={"x-api-key": "operator"}
            )
            assert result.status_code == 200
            body = result.json()
            assert body["run"]["status"] == "completed"
            assert body["report"]
            assert body.get("harness", {}).get("mode") == "incident"
            assert body["harness"]["variant"].startswith("sha256:")
            assert "harness_trace" not in body
    finally:
        await engine.dispose()


def test_cli_runtime_is_machine_readable_and_needs_no_optimizer(monkeypatch):
    import builtins

    from fin_graph.cli.main import app

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"better_harness", "deepagents"}:
            raise ImportError("optional optimizer not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    result = CliRunner().invoke(app, ["harness", "runtime"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["mode"] == "analysis"


def test_cli_default_investigation_uses_harness_variant_pin(monkeypatch):
    from fin_graph.cli.main import app
    from fin_graph.harness import runtime

    monkeypatch.setattr(runtime, "default_registry", lambda: IntegrationRegistry([], env_file=None))
    monkeypatch.setattr(runtime, "_services", lambda settings: UnavailableServices())
    runner = CliRunner()
    passed = runner.invoke(app, ["investigate", "--text", '{"title":"API error"}'])
    assert passed.exit_code == 0, passed.output
    assert "API error" in passed.output
    monkeypatch.setenv("FIN_HARNESS_EXPECTED_VARIANT", "wrong")
    rejected = runner.invoke(app, ["investigate", "--text", '{"title":"API error"}'])
    assert rejected.exit_code == 1
