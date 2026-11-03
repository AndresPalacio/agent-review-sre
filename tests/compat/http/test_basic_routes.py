from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from fin_graph.adapters.reference_app.http import reference_app_router
from fin_graph.adapters.reference_app.settings import ReferenceAppProfile


class FakeOllama:
    async def get_available_models(self) -> list[dict[str, str]]:
        return [
            {
                "display_name": "Llama 3.2 (3B)",
                "model_name": "llama3.2:3b",
                "provider": "Ollama",
            }
        ]


def app_for(
    tmp_path: Path,
    *,
    profile: ReferenceAppProfile | None = None,
) -> FastAPI:
    app = FastAPI()
    app.state.reference_app_profile = profile or ReferenceAppProfile()
    app.state.reference_app_store = None
    app.state.reference_app_export_dir = tmp_path / "outputs"
    app.state.reference_app_ollama = FakeOllama()

    async def key_role(key: str) -> str | None:
        return "viewer" if key == "valid-key" else None

    app.state.reference_app_key_role = key_role
    app.include_router(reference_app_router())
    return app


@asynccontextmanager
async def client_for(
    app: FastAPI,
    *,
    host: str = "127.0.0.1",
) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(host, 1234)),
        base_url="http://test",
    ) as client:
        yield client


async def test_health_root_ping_and_exact_language_model_catalog(tmp_path: Path) -> None:
    app = app_for(tmp_path)

    async with client_for(app) as client:
        root = await client.get("/")
        health = await client.get("/health")
        models = await client.get("/language-models/")
        providers = await client.get("/language-models/providers")

    assert root.json() == {"message": "Welcome to AI Hedge Fund API"}
    assert {key: health.json()[key] for key in ("status", "service", "version")} == {
        "status": "ok",
        "service": "ai-hedge-fund",
        "version": "0.1.0",
    }
    assert [item["model_name"] for item in models.json()["models"]] == [
        "claude-fable-5",
        "claude-opus-4-8",
        "grok-4.3",
        "deepseek-v4-pro",
        "gpt-5.5",
        "kimi-k2.6",
        "gemini-3.1-pro-preview",
        "llama3.2:3b",
    ]
    assert [item["name"] for item in providers.json()["providers"]] == [
        "Anthropic",
        "xAI",
        "DeepSeek",
        "OpenAI",
        "Kimi",
        "Google",
    ]


async def test_storage_is_atomic_and_confined_to_the_export_directory(tmp_path: Path) -> None:
    app = app_for(tmp_path)

    async with client_for(app) as client:
        saved = await client.post(
            "/storage/save-json",
            json={"filename": "analysis/result.json", "data": {"ticker": "AAPL"}},
        )
        traversal = await client.post(
            "/storage/save-json",
            json={"filename": "../escape.json", "data": {}},
        )
        absolute = await client.post(
            "/storage/save-json",
            json={"filename": "C:\\escape.json", "data": {}},
        )

    assert saved.status_code == 200
    target = tmp_path / "outputs" / "analysis" / "result.json"
    assert json.loads(target.read_text(encoding="utf-8")) == {"ticker": "AAPL"}
    assert traversal.status_code == absolute.status_code == 400
    assert not (tmp_path / "escape.json").exists()


async def test_skill_routes_preserve_unicode_and_reject_traversal(tmp_path: Path) -> None:
    app = app_for(tmp_path)

    async with client_for(app) as client:
        listed = await client.get("/v2/skills")
        selected = await client.get("/v2/skills/chanlun")
        resource = await client.get(
            "/v2/skills/chanlun/resources/references/%E6%A0%B8%E5%BF%83%E6%A6%82%E5%BF%B5/%E4%B8%AD%E6%9E%A2.md"
        )
        traversal = await client.get("/v2/skills/chanlun/resources/%2E%2E/SKILL.md")

    assert listed.status_code == 200 and len(listed.json()) == 71
    assert selected.json()["skill_id"] == "chanlun"
    assert selected.json()["body"].strip()
    assert resource.status_code == 200
    assert resource.json()["resource_path"] == "references/核心概念/中枢.md"
    assert traversal.status_code in {400, 404}


async def test_loopback_is_seamless_but_remote_clients_require_native_auth(tmp_path: Path) -> None:
    app = app_for(tmp_path)

    async with client_for(app) as local:
        assert (await local.get("/language-models/")).status_code == 200
    async with client_for(app, host="203.0.113.10") as remote:
        refused = await remote.get("/language-models/", headers={"x-forwarded-for": "127.0.0.1"})
        invalid = await remote.get("/language-models/", headers={"x-api-key": "bad"})
        allowed = await remote.get("/language-models/", headers={"x-api-key": "valid-key"})

    assert refused.status_code == invalid.status_code == 401
    assert allowed.status_code == 200


async def test_only_configured_proxies_may_supply_the_client_address(tmp_path: Path) -> None:
    profile = ReferenceAppProfile(trusted_proxies=("10.0.0.0/8",))
    app = app_for(tmp_path, profile=profile)

    async with client_for(app, host="10.1.2.3") as proxy:
        remote = await proxy.get("/language-models/", headers={"x-forwarded-for": "203.0.113.20"})
        local = await proxy.get("/language-models/", headers={"x-forwarded-for": "127.0.0.1"})

    assert remote.status_code == 401
    assert local.status_code == 200


async def test_explicit_insecure_opt_in_allows_remote_legacy_requests(tmp_path: Path) -> None:
    app = app_for(tmp_path, profile=ReferenceAppProfile(allow_insecure_remote=True))

    async with client_for(app, host="203.0.113.10") as client:
        response = await client.get("/language-models/")

    assert response.status_code == 200
