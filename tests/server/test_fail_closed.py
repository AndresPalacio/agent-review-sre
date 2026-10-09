from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import httpx
import pytest
from langsmith import utils as langsmith_utils
from sqlalchemy.ext.asyncio import create_async_engine

from sre_graph.server.app import create_app
from sre_graph.server.manager import RunManager
from sre_graph.server.security import WebhookVerifier
from sre_graph.server.storage import Base, Storage


def test_server_disables_external_tracing(tracing_enabled_by_environment: None) -> None:
    assert langsmith_utils.tracing_is_enabled()
    create_app()
    assert not langsmith_utils.tracing_is_enabled()


async def test_failed_submit_releases_claimed_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fail.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    manager = RunManager(None, storage, lambda: None)

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("simulated submit failure")

    monkeypatch.setattr(manager, "schedule_existing", fail)
    app = create_app(manager=manager, verifier=WebhookVerifier(tokens={"alertmanager": "token"}))
    payload = {
        "version": "4",
        "groupKey": "g",
        "alerts": [{"status": "firing", "labels": {"alertname": "A"}}],
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client,
    ):
        response = await client.post(
            "/v1/alerts/alertmanager", json=payload, headers={"x-sre-webhook-token": "token"}
        )
        assert response.status_code == 500
        from sre_graph.domain.alerts.parsing import parse_alert

        fingerprint = parse_alert(payload, source_hint="alertmanager").fingerprint
        assert await storage.fingerprint_thread("alertmanager", fingerprint) is None
    await engine.dispose()


async def test_retention_retries_after_listing_error(monkeypatch: pytest.MonkeyPatch) -> None:
    app_module = importlib.import_module("sre_graph.server.app")

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr(app_module.asyncio, "sleep", no_delay)

    class StorageStub:
        calls = 0

        async def expired_threads(self, cutoff: object) -> list[str]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient database failure")
            raise asyncio.CancelledError

    storage = StorageStub()
    manager = type("Manager", (), {"storage": storage})()
    from sre_graph.server.config import ServerConfig

    with pytest.raises(asyncio.CancelledError):
        await app_module._retention_loop(manager, object(), ServerConfig(_env_file=None))
    assert storage.calls == 2
