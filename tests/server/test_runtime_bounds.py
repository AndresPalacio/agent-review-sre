import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from tests.server.test_manager import ScriptedGraph

from sre_graph.server.manager import RunManager, RunOverloaded
from sre_graph.server.storage import Base, Storage


async def test_pending_admission_does_not_accept_extra_rows(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bounded.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    manager = RunManager(ScriptedGraph(), storage, lambda: None, max_concurrent=1, max_pending=2)
    first = await manager.submit("one", None)
    await manager.submit("two", None)
    with pytest.raises(RunOverloaded):
        await manager.submit("three", None)
    assert len(await storage.list_runs()) == 2
    await manager.stop()
    assert manager.pending_count == 0
    resumed = await manager.submit("four", None)
    assert resumed["run_id"] != first["run_id"]
    await manager.stop()
    await engine.dispose()


async def test_http_overload_precedes_webhook_claim(tmp_path: Path):
    import httpx

    from sre_graph.server.app import create_app
    from sre_graph.server.security import WebhookVerifier

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'webhook.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("operator", "operator")
    manager = RunManager(ScriptedGraph(), storage, lambda: None, max_pending=1)
    app = create_app(manager=manager, verifier=WebhookVerifier(tokens={"alertmanager": "token"}))
    alert = {
        "version": "4",
        "groupKey": "test",
        "alerts": [{"status": "firing", "labels": {"alertname": "CPU"}}],
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        await manager.submit("busy", None)
        response = await client.post(
            "/v1/alerts/alertmanager", json=alert, headers={"x-sre-webhook-token": "token"}
        )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"
        assert len(await storage.list_runs()) == 1
        await manager.stop()
        response = await client.post(
            "/v1/alerts/alertmanager", json=alert, headers={"x-sre-webhook-token": "token"}
        )
        assert response.status_code == 202 and not response.json()["deduplicated"]
    await engine.dispose()


async def test_burst_and_recovery_overload_release_capacity(tmp_path: Path):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from sre_graph.server.storage import RunRow

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'burst.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    manager = RunManager(ScriptedGraph(), storage, lambda: None, max_pending=2, max_concurrent=1)
    try:
        results = await asyncio.gather(
            *(manager.submit(str(i), None) for i in range(30)), return_exceptions=True
        )
        assert sum(isinstance(result, dict) for result in results) == 2
        assert sum(isinstance(result, RunOverloaded) for result in results) == 28
        await storage.create_run("orphan", "orphan-thread")
        async with engine.begin() as conn:
            await conn.execute(
                update(RunRow)
                .where(RunRow.run_id == "orphan")
                .values(updated_at=datetime.now(UTC) - timedelta(minutes=3))
            )
        with pytest.raises(RunOverloaded):
            await manager.recover_existing("orphan", "orphan-thread", "alert")
        assert (await storage.get_run("orphan"))["status"] == "queued"
        await manager.stop()
        assert manager.pending_count == 0
        for result in results:
            if isinstance(result, dict):
                assert (await storage.get_run(result["run_id"]))["status"] == "interrupted"

    finally:
        await manager.stop()
        await engine.dispose()


async def test_scheduling_failure_releases_slot_and_marks_row(tmp_path: Path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'failure.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    manager = RunManager(ScriptedGraph(), storage, lambda: None, max_pending=1)

    def fail(*args, **kwargs):
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(manager, "schedule_existing", fail)
    with pytest.raises(RuntimeError):
        await manager.submit("alert", None)
    assert manager.pending_count == 0
    assert (await storage.list_runs())[0]["status"] == "failed"
    await engine.dispose()
