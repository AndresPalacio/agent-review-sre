from __future__ import annotations

from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from tests.web.fixture_server import create_fixture_app
from tests.web.reference_frontend_server import resolve_reference_frontend

from fin_graph.compat.storage import FernetSecretCipher, open_compat_sqlite


def test_frozen_reference_frontend_is_discovered_without_source_changes() -> None:
    frontend = resolve_reference_frontend()
    assert (frontend / "package.json").is_file()
    flow_service = (frontend / "src/services/flow-service.ts").read_text(encoding="utf-8")
    api_service = (frontend / "src/services/api.ts").read_text(encoding="utf-8")
    assert "http://localhost:8000" in flow_service
    for route in ("/flows/", "/hedge-fund/agents", "/hedge-fund/run"):
        assert route in flow_service + api_service


async def test_fixture_serves_the_reference_flow_and_stream_contract(tmp_path: Path) -> None:
    cipher = FernetSecretCipher(Fernet.generate_key())
    async with open_compat_sqlite(tmp_path / "compatibility.sqlite", cipher) as store:
        app = await create_fixture_app(tmp_path / "native.sqlite", store, tmp_path / "artifacts")
        try:
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
                ) as client,
            ):
                assert (await client.get("/hedge-fund/agents")).status_code == 200
                assert (await client.get("/language-models/")).status_code == 200
                created = await client.post(
                    "/flows/",
                    json={
                        "name": "Reference contract",
                        "nodes": [
                            {
                                "id": "valuation_analyst",
                                "type": "agent-node",
                                "position": {"x": 10, "y": 10},
                                "data": {"name": "Valuation"},
                            }
                        ],
                        "edges": [],
                    },
                )
                assert created.status_code == 200
                assert (await client.get(f"/flows/{created.json()['id']}")).status_code == 200
                streamed = await client.post(
                    "/hedge-fund/run",
                    json={
                        "tickers": ["AAPL"],
                        "graph_nodes": created.json()["nodes"],
                        "graph_edges": [],
                    },
                )
                assert streamed.status_code == 200
                assert "event: complete" in streamed.text
                assert '"action":"buy"' in streamed.text
        finally:
            await app.state.fixture_engine.dispose()
