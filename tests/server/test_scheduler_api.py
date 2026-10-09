from __future__ import annotations

from pathlib import Path

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import create_async_engine

from sre_graph.graphs.investigation.graph import make_investigation_graph
from sre_graph.graphs.investigation.services import UnavailableServices
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.registry import IntegrationRegistry
from sre_graph.server.app import create_app
from sre_graph.server.config import ServerConfig
from sre_graph.server.manager import RunManager
from sre_graph.server.storage import Base, Storage


class SchedulerStub:
    def __init__(self) -> None:
        self.is_leader = True
        self.synced: list[str] = []
        self.ran: list[str] = []

    async def sync_schedule(self, schedule_id: str) -> None:
        self.synced.append(schedule_id)

    async def run_schedule(self, schedule_id: str) -> None:
        self.ran.append(schedule_id)


def test_approval_ttl_is_positive() -> None:
    assert ServerConfig(_env_file=None).approval_ttl_hours == 24
    from pydantic import ValidationError

    try:
        ServerConfig(_env_file=None, approval_ttl_hours=0)
    except ValidationError:
        pass
    else:
        raise AssertionError("zero-hour approval TTL was accepted")


async def test_schedule_api_requires_roles_and_syncs_enabled_definition(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'schedules.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("viewer", "viewer")
    await storage.put_key("operator", "operator")
    await storage.put_key("admin", "admin")
    registry = IntegrationRegistry([], env_file=None)
    graph = make_investigation_graph(UnavailableServices(), registry, checkpointer=InMemorySaver())
    manager = RunManager(graph, storage, RunContext)
    scheduler = SchedulerStub()
    app = create_app(manager=manager, registry=registry, scheduler_service=scheduler)
    definition = {
        "kind": "health_check",
        "interval_minutes": 5,
        "checks": [{"id": "up", "expression": "up", "operator": "lt", "threshold": 1}],
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/v1/schedules")).status_code == 401
        assert (await client.get("/v1/schedules", headers={"x-api-key": "viewer"})).json() == []
        assert (
            await client.post(
                "/v1/schedules",
                json={"id": "cpu", "definition": definition},
                headers={"x-api-key": "operator"},
            )
        ).status_code == 403
        created = await client.post(
            "/v1/schedules",
            json={"id": "cpu", "definition": definition, "enabled": True},
            headers={"x-api-key": "admin"},
        )
        assert created.status_code == 201, created.text
        assert scheduler.synced == ["cpu"]
        assert (await client.get("/v1/schedules/cpu", headers={"x-api-key": "viewer"})).json()[
            "enabled"
        ] is True
        assert (
            await client.post("/v1/schedules/cpu/run", headers={"x-api-key": "operator"})
        ).status_code == 200
        assert scheduler.ran == ["cpu"]
        assert (
            await client.delete("/v1/schedules/cpu", headers={"x-api-key": "admin"})
        ).status_code == 204
        assert scheduler.synced == ["cpu", "cpu"]
    await engine.dispose()


async def test_schedule_api_rejects_invalid_definition_without_persisting(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'invalid.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("admin", "admin")
    registry = IntegrationRegistry([], env_file=None)
    graph = make_investigation_graph(UnavailableServices(), registry, checkpointer=InMemorySaver())
    app = create_app(
        manager=RunManager(graph, storage, RunContext),
        registry=registry,
        scheduler_service=SchedulerStub(),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        result = await client.post(
            "/v1/schedules",
            json={
                "id": "bad",
                "definition": {"kind": "digest", "hour": 27, "minute": 0, "timezone": "UTC"},
            },
            headers={"x-api-key": "admin"},
        )
        assert result.status_code == 422
        assert await storage.get_schedule("bad") is None
    await engine.dispose()


async def test_schedule_write_auth_precedes_body_validation(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    registry = IntegrationRegistry([], env_file=None)
    graph = make_investigation_graph(UnavailableServices(), registry, checkpointer=InMemorySaver())
    app = create_app(
        manager=RunManager(graph, storage, RunContext),
        registry=registry,
        scheduler_service=SchedulerStub(),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        for method, path in (("POST", "/v1/schedules"), ("PUT", "/v1/schedules/cpu")):
            response = await client.request(method, path, content=b"not json")
            assert response.status_code == 401
    await engine.dispose()
