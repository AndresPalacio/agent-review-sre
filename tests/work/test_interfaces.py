"""Real HTTP and MCP transports over one local service and command ledger."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from fin_graph.work.contracts import StartWork
from fin_graph.work.service import WorkService
from fin_graph.work.store import WorkStore


class ControlledEngine:
    def __init__(self) -> None:
        self.submissions: list[dict[str, Any]] = []
        self.states: dict[str, dict[str, Any]] = {}
        self.unavailable = False

    async def ensure_thread(self, work_id: str, metadata: dict[str, Any]) -> None:
        self.states.setdefault(work_id, {"status": "idle", "values": {}, "children": []})

    async def submit(
        self,
        work_id: str,
        text: str,
        command_id: str,
        context: dict[str, Any],
        *,
        enqueue: bool = False,
    ) -> str:
        if self.unavailable:
            raise ConnectionError("provider-secret-must-not-leak")
        run_id = f"run-{len(self.submissions) + 1}"
        self.submissions.append({"command_id": command_id, "work_id": work_id, "run_id": run_id})
        self.states[work_id] = {
            "status": "busy",
            "values": {"work_revision": context["revision"]},
            "children": [],
        }
        return run_id

    async def find_run(self, work_id: str, command_id: str) -> str | None:
        return next(
            (
                item["run_id"]
                for item in self.submissions
                if item["work_id"] == work_id and item["command_id"] == command_id
            ),
            None,
        )

    async def snapshot(self, work_id: str) -> dict[str, Any]:
        return self.states[work_id]

    async def cancel_tree(self, work_id: str) -> bool:
        self.states[work_id]["status"] = "idle"
        return True


@pytest.fixture
def gateway(tmp_path: Path) -> tuple[WorkService, ControlledEngine]:
    engine = ControlledEngine()
    return WorkService(WorkStore(tmp_path / "interfaces.sqlite"), engine), engine


@asynccontextmanager
async def http_gateway(
    service: WorkService, *, actor: str = "ana"
) -> AsyncIterator[httpx.AsyncClient]:
    from fin_graph.work.api import create_app

    app = create_app(service, actor=actor)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
            follow_redirects=True,
        ) as client,
    ):
        yield client


async def test_http_lifecycle_uses_configured_actor_and_shared_contracts(
    gateway: tuple[WorkService, ControlledEngine],
) -> None:
    service, engine = gateway
    async with http_gateway(service) as client:
        started = await client.post(
            "/work",
            json={"command_id": "start", "text": "Investiga checkout"},
            headers={"X-Actor": "mallory", "Origin": "https://untrusted.invalid"},
        )
        assert started.status_code == 200
        work = started.json()
        assert service.store.get(work["work_id"])["actor"] == "ana"
        assert "access-control-allow-origin" not in started.headers
        assert work["revision"] == 1
        assert work["decision"]["route"] == "investigate"

        updated = await client.post(
            f"/work/{work['work_id']}/update",
            json={"command_id": "update", "expected_revision": 1, "message": "Revisa logs"},
        )
        assert updated.status_code == 200
        assert updated.json()["revision"] == 2

        cancelled = await client.post(
            f"/work/{work['work_id']}/cancel",
            json={"command_id": "cancel", "expected_revision": 2},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["revision"] == 3
        assert cancelled.json()["status"] == "cancelled"
        fetched = await client.get(f"/work/{work['work_id']}")
        assert fetched.json() == cancelled.json()

    assert len(engine.submissions) == 2


async def test_http_translates_conflict_permission_and_unavailable_errors(
    gateway: tuple[WorkService, ControlledEngine],
) -> None:
    service, engine = gateway
    other = await service.start(
        StartWork(command_id="foreign", text="Investiga pagos"), actor="bob"
    )
    async with http_gateway(service) as client:
        denied = await client.get(f"/work/{other.work_id}", headers={"X-Actor": "bob"})
        assert denied.status_code == 403
        assert (await client.get("/work/missing")).status_code == 403

        work = (
            await client.post("/work", json={"command_id": "own", "text": "Investiga checkout"})
        ).json()
        stale = await client.post(
            f"/work/{work['work_id']}/update",
            json={"command_id": "stale", "expected_revision": 2, "message": "Revisa logs"},
        )
        assert stale.status_code == 409

        engine.unavailable = True
        unavailable = await client.post(
            "/work", json={"command_id": "unavailable", "text": "Investiga checkout"}
        )
        assert unavailable.status_code == 503
        assert "provider-secret" not in unavailable.text


@pytest.mark.parametrize(
    "command",
    [
        {"command_id": "empty", "text": ""},
        {"command_id": "actor", "text": "hola", "actor": "bob"},
        {"command_id": "deadline", "text": "hola", "deadline_seconds": 0},
    ],
)
async def test_http_validates_contract_before_execution(
    gateway: tuple[WorkService, ControlledEngine], command: dict[str, Any]
) -> None:
    service, engine = gateway
    async with http_gateway(service) as client:
        result = await client.post("/work", json=command)
    assert result.status_code == 422
    assert engine.submissions == []


@asynccontextmanager
async def mcp_session(client: httpx.AsyncClient) -> AsyncIterator[ClientSession]:
    async with (
        streamable_http_client("http://127.0.0.1:8000/mcp/", http_client=client) as (
            read_stream,
            write_stream,
            _session_id,
        ),
        ClientSession(
            read_stream, write_stream, read_timeout_seconds=timedelta(seconds=5)
        ) as session,
    ):
        await session.initialize()
        yield session


def tool_view(result: CallToolResult) -> dict[str, Any]:
    assert not result.isError, result
    assert result.structuredContent is not None
    return result.structuredContent


async def test_mcp_lists_and_calls_all_tools_against_the_same_http_service(
    gateway: tuple[WorkService, ControlledEngine],
) -> None:
    service, engine = gateway
    async with http_gateway(service) as client, mcp_session(client) as session:
        tools = (await session.list_tools()).tools
        assert {tool.name for tool in tools} == {
            "work_start",
            "work_get",
            "work_update",
            "work_cancel",
        }
        for tool in tools:
            assert "actor" not in tool.inputSchema["properties"]

        command = {"command_id": "shared-start", "text": "Investiga checkout"}
        started = (await client.post("/work", json=command)).json()
        duplicate = tool_view(await session.call_tool("work_start", {"command": command}))
        assert duplicate["work_id"] == started["work_id"]
        assert len(engine.submissions) == 1

        work_id = started["work_id"]
        assert tool_view(await session.call_tool("work_get", {"work_id": work_id})) == duplicate
        update = {"command_id": "shared-update", "expected_revision": 1, "message": "Revisa logs"}
        updated = tool_view(
            await session.call_tool("work_update", {"work_id": work_id, "command": update})
        )
        assert updated["revision"] == 2
        repeated = await client.post(f"/work/{work_id}/update", json=update)
        assert repeated.status_code == 200
        assert repeated.json() == updated
        assert len(engine.submissions) == 2

        cancelled = tool_view(
            await session.call_tool(
                "work_cancel",
                {"work_id": work_id, "command": {"command_id": "cancel", "expected_revision": 2}},
            )
        )
        assert cancelled["status"] == "cancelled"
        assert cancelled["revision"] == 3
        assert (await client.get(f"/work/{work_id}")).json() == cancelled
        assert service.store.get(work_id)["actor"] == "ana"


async def test_mcp_enforces_actor_revision_and_contract_errors(
    gateway: tuple[WorkService, ControlledEngine],
) -> None:
    service, engine = gateway
    foreign = await service.start(
        StartWork(command_id="foreign", text="Investiga pagos"), actor="bob"
    )
    async with http_gateway(service) as client, mcp_session(client) as session:
        forbidden = await session.call_tool(
            "work_get", {"work_id": foreign.work_id, "actor": "bob"}
        )
        assert forbidden.isError
        assert "403" in forbidden.model_dump_json()

        invalid = await session.call_tool(
            "work_start", {"command": {"command_id": "invalid", "text": "hola", "actor": "bob"}}
        )
        assert invalid.isError
        assert len(engine.submissions) == 1

        started = tool_view(
            await session.call_tool(
                "work_start", {"command": {"command_id": "own", "text": "Investiga checkout"}}
            )
        )
        stale = await session.call_tool(
            "work_update",
            {
                "work_id": started["work_id"],
                "command": {
                    "command_id": "stale",
                    "expected_revision": 2,
                    "message": "Revisa logs",
                },
            },
        )
        assert stale.isError
        assert "409" in stale.model_dump_json()
        assert len(engine.submissions) == 2

        engine.unavailable = True
        unavailable = await session.call_tool(
            "work_start", {"command": {"command_id": "unavailable", "text": "Investiga checkout"}}
        )
        assert unavailable.isError
        assert "503" in unavailable.model_dump_json()
        assert "provider-secret" not in unavailable.model_dump_json()
