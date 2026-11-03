from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from fin_graph.persistence.reference_app import ReferenceStore
from fin_graph.server.factory import RuntimeProfile, create_app


async def _client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


async def test_clean_local_startup_needs_neither_postgres_nor_admin_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_KEY", raising=False)
    app = create_app(RuntimeProfile.local(data_dir=tmp_path))

    async with app.router.lifespan_context(app), await _client(app) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.get("/ready")).json() == {"status": "ready"}
        assert (await client.get("/language-models/")).status_code == 200
        assert isinstance(app.state.reference_app_store, ReferenceStore)
        assert app.state.runtime_capabilities == {
            "backend": "sqlite",
            "distributed_locks": False,
            "multi_worker": False,
        }

    assert app.state.manager._reconcile_task is None


async def test_local_compatibility_data_survives_restart(tmp_path: Path) -> None:
    first = create_app(RuntimeProfile.local(data_dir=tmp_path))
    async with first.router.lifespan_context(first):
        flow, _revision = await first.state.reference_app_store.create_flow(
            "persistent-flow", {"nodes": []}
        )

    second = create_app(RuntimeProfile.local(data_dir=tmp_path))
    async with second.router.lifespan_context(second):
        persisted = await second.state.reference_app_store.get_flow(flow.id)

    assert persisted is not None
    assert persisted.name == "persistent-flow"
    assert (tmp_path / "compatibility.sqlite").is_file()
    assert (tmp_path / "runtime.sqlite").is_file()
    assert (tmp_path / "compatibility.key").is_file()


def test_local_profile_refuses_remote_legacy_bind_by_default(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        RuntimeProfile.local(data_dir=tmp_path, host="0.0.0.0", allow_insecure_remote=False)

    profile = RuntimeProfile.local(
        data_dir=tmp_path,
        host="0.0.0.0",
        allow_insecure_remote=True,
    )
    assert profile.host == "0.0.0.0"


def test_launch_scripts_and_docker_profiles_delegate_to_supported_commands() -> None:
    root = Path(__file__).parents[2]
    assert "fin-graph app" in (root / "app" / "run.bat").read_text(encoding="utf-8")
    assert "fin-graph app" in (root / "app" / "run.sh").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "app:" in compose
    assert "distributed" in compose
    assert "FIN_GRAPH_ALLOW_INSECURE_LEGACY_API" in compose
