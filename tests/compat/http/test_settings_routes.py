from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI

from fin_graph.compat.settings import CompatibilityProfile
from fin_graph.compat.storage import FernetSecretCipher, open_compat_sqlite
from fin_graph.server.compat import compatibility_router


class FakeOllama:
    def __init__(self) -> None:
        self.progress = {"llama3.2:3b": {"status": "downloading", "percentage": 25.0}}

    async def check_ollama_status(self) -> dict[str, object]:
        return {
            "installed": True,
            "running": True,
            "available_models": ["llama3.2:3b"],
            "server_url": "http://localhost:11434",
            "error": None,
        }

    async def start_server(self) -> dict[str, object]:
        return {"success": True, "message": "started"}

    async def stop_server(self) -> dict[str, object]:
        return {"success": True, "message": "stopped"}

    async def download_model(self, model_name: str) -> dict[str, object]:
        return {"success": True, "message": f"downloaded {model_name}"}

    async def download_model_with_progress(self, model_name: str) -> AsyncIterator[str]:
        yield 'data: {"status":"downloading","percentage":50}\n\n'
        yield 'data: {"status":"completed","percentage":100}\n\n'

    def get_download_progress(self, model_name: str) -> dict[str, object] | None:
        return self.progress.get(model_name)

    def get_all_download_progress(self) -> dict[str, dict[str, object]]:
        return dict(self.progress)

    async def delete_model(self, model_name: str) -> dict[str, object]:
        return {"success": True, "message": f"deleted {model_name}"}

    async def get_recommended_models(self) -> list[dict[str, str]]:
        return [
            {
                "display_name": "Llama 3.2 (3B)",
                "model_name": "llama3.2:3b",
                "provider": "Meta",
            }
        ]

    async def get_available_models(self) -> list[dict[str, str]]:
        return []

    def cancel_download(self, model_name: str) -> bool:
        return self.progress.pop(model_name, None) is not None


@asynccontextmanager
async def configured_client(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Path]]:
    database = tmp_path / "compat.sqlite"
    async with open_compat_sqlite(database, FernetSecretCipher(Fernet.generate_key())) as store:
        app = FastAPI()
        app.state.compatibility_profile = CompatibilityProfile()
        app.state.compatibility_store = store
        app.state.compatibility_export_dir = tmp_path / "outputs"
        app.state.compatibility_ollama = FakeOllama()
        app.state.compatibility_key_role = None
        app.include_router(compatibility_router())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url="http://test",
        ) as client:
            yield client, database


async def test_provider_key_crud_is_redacted_encrypted_and_tracks_metadata(tmp_path: Path) -> None:
    secret = "sk-provider-secret-value"
    async with configured_client(tmp_path) as (client, database):
        created = await client.post(
            "/api-keys/",
            json={
                "provider": "OPENAI_API_KEY",
                "key_value": secret,
                "description": "primary",
                "is_active": True,
            },
        )
        listed = await client.get("/api-keys/")
        fetched = await client.get("/api-keys/OPENAI_API_KEY")
        updated = await client.put(
            "/api-keys/OPENAI_API_KEY",
            json={"description": "rotated", "key_value": "sk-rotated-secret"},
        )
        deactivated = await client.patch("/api-keys/OPENAI_API_KEY/deactivate")
        touched = await client.patch("/api-keys/OPENAI_API_KEY/last-used")
        hidden = await client.get("/api-keys/")
        inactive = await client.get("/api-keys/?include_inactive=true")
        deleted = await client.delete("/api-keys/OPENAI_API_KEY")
        missing = await client.get("/api-keys/OPENAI_API_KEY")

    assert created.status_code == 200
    assert secret not in created.text and created.json()["key_value"].endswith("alue")
    assert len(listed.json()) == 1 and "key_value" not in listed.json()[0]
    assert secret not in fetched.text
    assert updated.json()["description"] == "rotated"
    assert not deactivated.json()["is_active"]
    assert touched.status_code == 200
    assert hidden.json() == [] and len(inactive.json()) == 1
    assert inactive.json()[0]["last_used"] is not None
    assert deleted.status_code == 204 and missing.status_code == 404
    raw = database.read_bytes()
    assert secret.encode() not in raw and b"sk-rotated-secret" not in raw


async def test_bulk_keys_and_unsafe_provider_names_are_bounded(tmp_path: Path) -> None:
    async with configured_client(tmp_path) as (client, _):
        bulk = await client.post(
            "/api-keys/bulk",
            json={
                "api_keys": [
                    {"provider": "ANTHROPIC_API_KEY", "key_value": "one"},
                    {"provider": "GROQ_API_KEY", "key_value": "two"},
                ]
            },
        )
        traversal = await client.get("/api-keys/%2E%2E%2Fsecret")

    assert bulk.status_code == 200 and len(bulk.json()) == 2
    assert "one" not in bulk.text and "two" not in bulk.text
    assert traversal.status_code in {400, 404}


async def test_ollama_management_and_progress_contracts(tmp_path: Path) -> None:
    async with configured_client(tmp_path) as (client, _):
        status = await client.get("/ollama/status")
        started = await client.post("/ollama/start")
        stopped = await client.post("/ollama/stop")
        downloaded = await client.post(
            "/ollama/models/download", json={"model_name": "llama3.2:3b"}
        )
        streamed = await client.post(
            "/ollama/models/download/progress", json={"model_name": "llama3.2:3b"}
        )
        progress = await client.get("/ollama/models/download/progress/llama3.2:3b")
        active = await client.get("/ollama/models/downloads/active")
        recommended = await client.get("/ollama/models/recommended")
        deleted = await client.delete("/ollama/models/llama3.2:3b")
        cancelled = await client.delete("/ollama/models/download/llama3.2:3b")
        unsafe = await client.delete("/ollama/models/%2E%2E%2Fsecret")

    assert status.json()["running"] is True
    assert started.json()["success"] and stopped.json()["success"]
    assert downloaded.json()["success"]
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert "completed" in streamed.text
    assert progress.json()["percentage"] == 25
    assert "llama3.2:3b" in active.json()
    assert recommended.json()[0]["model_name"] == "llama3.2:3b"
    assert deleted.json()["success"] and cancelled.json()["success"]
    assert unsafe.status_code in {400, 404}
