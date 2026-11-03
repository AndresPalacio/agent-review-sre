from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI

from fin_graph.compat.settings import CompatibilityProfile
from fin_graph.compat.storage import FernetSecretCipher, open_compat_sqlite
from fin_graph.flows.service import FlowService, FlowServices
from fin_graph.server.compat import compatibility_router


def definition(name: str = "Portfolio flow") -> dict[str, object]:
    return {
        "name": name,
        "description": "fan out",
        "nodes": [
            {"id": "input", "type": "stock-analyzer-node", "data": {"name": "Input"}},
            {"id": "valuation", "type": "agent-node", "data": {"agent_id": "valuation"}},
            {"id": "output", "type": "json-output-node", "data": {"name": "Output"}},
        ],
        "edges": [
            {"id": "a", "source": "input", "target": "valuation"},
            {"id": "b", "source": "valuation", "target": "output"},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
        "data": {"owner": "test"},
        "is_template": False,
        "tags": ["quality"],
    }


@asynccontextmanager
async def client_for(database: Path) -> AsyncIterator[httpx.AsyncClient]:
    cipher = FernetSecretCipher(Fernet.generate_key())
    async with open_compat_sqlite(database, cipher) as store:

        async def execute(
            node: object, inputs: dict[str, object], request: dict[str, object]
        ) -> object:
            return {"signal": "neutral", "inputs": list(inputs), "request": request}

        app = FastAPI()
        app.state.compatibility_profile = CompatibilityProfile()
        app.state.compatibility_store = store
        app.state.compatibility_flow_service = FlowService(
            store, FlowServices(execute=execute), schedule_runs=False
        )
        app.include_router(compatibility_router())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


async def test_flow_crud_duplicate_search_and_revision_pinning(tmp_path: Path) -> None:
    database = tmp_path / "flows.sqlite"
    async with client_for(database) as client:
        created = await client.post("/flows/", json=definition())
        assert created.status_code == 200
        flow = created.json()
        flow_id = flow["id"]
        assert flow_id <= 9_007_199_254_740_991

        listed = await client.get("/flows/", params={"include_templates": "false"})
        searched = await client.get("/flows/search/portfolio")
        duplicate = await client.post(f"/flows/{flow_id}/duplicate", params={"new_name": "Copy"})
        updated = await client.put(
            f"/flows/{flow_id}", json={"name": "Changed", "tags": ["updated"]}
        )
        run = await client.post(
            f"/flows/{flow_id}/runs/", json={"request_data": {"tickers": ["AAPL"]}}
        )

        assert listed.json()[0]["id"] == searched.json()[0]["id"] == flow_id
        assert duplicate.json()["name"] == "Copy"
        assert updated.json()["nodes"] == flow["nodes"]
        assert run.status_code == 200 and run.json()["run_number"] == 1

        service = client._transport.app.state.compatibility_flow_service  # type: ignore[attr-defined]
        stored = await service.get_run(flow_id, run.json()["id"])
        assert stored.flow_revision == 2

    async with client_for(database) as restarted:
        persisted = await restarted.get(f"/flows/{flow_id}")
        assert persisted.json()["name"] == "Changed"


async def test_flow_run_history_cycles_pagination_and_idempotent_cancel(tmp_path: Path) -> None:
    async with client_for(tmp_path / "runs.sqlite") as client:
        flow_id = (await client.post("/flows/", json=definition())).json()["id"]
        first = (
            await client.post(
                f"/flows/{flow_id}/runs/", json={"request_data": {"tickers": ["AAPL"]}}
            )
        ).json()
        await client.post(f"/flows/{flow_id}/runs/", json={"request_data": {"tickers": ["MSFT"]}})
        service = client._transport.app.state.compatibility_flow_service  # type: ignore[attr-defined]
        await service.append_cycle(first["id"], "analyst.started", {"agent": "valuation"})

        page = await client.get(f"/flows/{flow_id}/runs/", params={"limit": 1, "offset": 1})
        cycles = await client.get(f"/flows/{flow_id}/runs/{first['id']}/cycles")
        cancelled = await client.post(f"/flows/{flow_id}/runs/{first['id']}/cancel")
        cancelled_again = await client.post(f"/flows/{flow_id}/runs/{first['id']}/cancel")
        count = await client.get(f"/flows/{flow_id}/runs/count")

        assert len(page.json()) == 1
        assert cycles.json()[0]["event"] == "analyst.started"
        assert cancelled.json()["status"] == cancelled_again.json()["status"] == "ERROR"
        assert count.json() == {"flow_id": flow_id, "total_runs": 2}


async def test_flow_errors_and_delete_semantics(tmp_path: Path) -> None:
    async with client_for(tmp_path / "errors.sqlite") as client:
        flow_id = (await client.post("/flows/", json=definition())).json()["id"]
        conflict = await client.post("/flows/", json=definition())
        missing = await client.get("/flows/999999999")
        deleted = await client.delete(f"/flows/{flow_id}")
        deleted_again = await client.delete(f"/flows/{flow_id}")

    assert conflict.status_code == 409
    assert missing.status_code == deleted_again.status_code == 404
    assert deleted.status_code == 200
