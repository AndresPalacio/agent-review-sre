from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from tests.server.finance_app import REQUEST
from tests.server.test_orders_api import ORDER
from tests.web.fixture_server import create_fixture_app


async def test_fixture_construction_failure_disposes_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_chat(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated graph construction failure")

    monkeypatch.setattr("tests.web.fixture_server.make_chat_graph", fail_chat)
    database = tmp_path / "failed.sqlite"
    with pytest.raises(RuntimeError, match="simulated graph construction failure"):
        await create_fixture_app(database)
    # On Windows an undisposed SQLite pool leaves this file locked.
    database.unlink()
    assert not database.exists()


async def test_offline_fixture_runs_real_graph_and_audited_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "openai:must-never-be-created")
    monkeypatch.setenv("MASK_ENABLED", "true")
    monkeypatch.setenv("MASK_EXTRA_REGEX", "invalid-json-that-must-be-ignored")
    monkeypatch.setenv("CORS_ORIGINS", "https://must-never-be-allowed.example")
    monkeypatch.setenv("WEBHOOK_TOKENS", "invalid-json-that-must-be-ignored")
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "not-an-integer")

    def forbidden_provider(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline fixture tried to construct a provider model")

    monkeypatch.setattr("langchain.chat_models.init_chat_model", forbidden_provider)
    app = await create_fixture_app(tmp_path / "fixture.sqlite")
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://fixture"
            ) as client,
        ):
            headers = {"x-api-key": "admin-key"}
            response = await client.post(
                "/v1/analyses",
                json={"request": REQUEST},
                headers=headers,
            )
            assert response.status_code == 202, response.text
            thread = response.json()["thread_id"]
            for _ in range(100):
                detail = (await client.get(f"/v1/analyses/{thread}", headers=headers)).json()
                if detail["run"]["status"] in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.02)
            assert detail["run"]["status"] == "completed", detail
            assert detail["report"]["sections"][0]["ticker"] == "DEEP"
            assert detail["evidence"][0]["tool"].startswith("compute_")
            assert detail["evidence"][0]["data"]
            queued = await client.post(
                "/v1/orders",
                json=ORDER,
                headers=headers,
            )
            assert queued.status_code == 202, queued.text
            pending = queued.json()
            assert pending["approval_ids"]
            state = (await client.get("/__fixture/state")).json()
            assert state["portfolio"]["positions"] == {}
            assert state["orders"] == []
            thread = pending["thread_id"]
            decision = {"decisions": [{"type": "approve"}], "approval_ids": pending["approval_ids"]}
            approved = await client.post(
                f"/v1/orders/{thread}/approve", json=decision, headers=headers
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["outcome"] == "filled"
            repeated = await client.post(
                f"/v1/orders/{thread}/approve", json=decision, headers=headers
            )
            assert repeated.status_code == 409
            state = (await client.get("/__fixture/state", params={"thread_id": thread})).json()
            assert state["portfolio"]["positions"]["DEEP"]["long"] == 10
            assert len(state["orders"]) == 1
            assert state["approvals"][0]["decision"] == "approve"
            assert state["approvals"][0]["action_name"] == "place_paper_order"
            queued = await client.post(
                "/v1/orders",
                json={**ORDER, "quantity": 3},
                headers=headers,
            )
            pending = queued.json()
            rejected = await client.post(
                f"/v1/orders/{pending['thread_id']}/approve",
                json={"decisions": [{"type": "reject"}], "approval_ids": pending["approval_ids"]},
                headers=headers,
            )
            assert rejected.json()["outcome"] == "rejected"
            state = (
                await client.get("/__fixture/state", params={"thread_id": pending["thread_id"]})
            ).json()
            assert len(state["orders"]) == 1
            assert state["approvals"][0]["decision"] == "reject"
            viewer = await client.post(
                "/v1/orders",
                json={**ORDER, "quantity": 3},
                headers={"x-api-key": "viewer-key"},
            )
            assert viewer.status_code == 403
            reply = await client.post(
                "/v1/chat/threads/fixture-chat/messages",
                json={"message": "Estado"},
                headers=headers,
            )
            assert "event: message" in reply.text, reply.text
            history = (await client.get("/v1/chat/threads/fixture-chat", headers=headers)).json()
            assert [item["role"] for item in history["messages"]] == ["user", "assistant"]
    finally:
        await app.state.fixture_engine.dispose()
