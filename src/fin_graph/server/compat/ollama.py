"""Legacy Ollama lifecycle and model-management routes over an injected bounded service."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from fin_graph.compat.ai_hedge_fund.schemas import (
    ActionResponse,
    ModelRequest,
    OllamaStatusResponse,
    ProgressResponse,
    RecommendedModel,
)

router = APIRouter(prefix="/ollama")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

_RECOMMENDED = (
    ("Llama 3.2 (1B)", "llama3.2:1b", "Meta"),
    ("Llama 3.2 (3B)", "llama3.2:3b", "Meta"),
    ("gpt-oss (20B)", "gpt-oss:20b", "OpenAI"),
    ("gpt-oss (120B)", "gpt-oss:120b", "OpenAI"),
    ("Gemma 3 (4B)", "gemma3:4b", "Google"),
    ("Qwen 3 (4B)", "qwen3:4b", "Alibaba"),
    ("Qwen 3 (8B)", "qwen3:8b", "Alibaba"),
    ("Llama 3.1 (8B)", "llama3.1:latest", "Meta"),
    ("Gemma 3 (12B)", "gemma3:12b", "Google"),
    ("Mistral Small 3.1 (24B)", "mistral-small3.1", "Mistral"),
    ("Gemma 3 (27B)", "gemma3:27b", "Google"),
    ("Qwen 3 (30B-a3B)", "qwen3:30b-a3b", "Alibaba"),
    ("Llama 3.3 (70B)", "llama3.3:70b-instruct-q4_0", "Meta"),
)


class OllamaCompatibilityService:
    """Small bounded client; process control is limited to the server this instance starts."""

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._process: subprocess.Popen[bytes] | None = None
        self._progress: dict[str, dict[str, Any]] = {}
        self._cancelled: set[str] = set()

    async def check_ollama_status(self) -> dict[str, object]:
        executable = shutil.which("ollama")
        models: list[str] = []
        running = False
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout, 2.0)) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                payload = response.json()
            models = [
                str(item["name"])
                for item in payload.get("models", [])
                if isinstance(item, dict) and item.get("name")
            ]
            running = True
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        return {
            "installed": executable is not None,
            "running": running,
            "server_running": running,
            "available_models": models,
            "server_url": self.base_url,
            "error": None,
        }

    async def get_available_models(self) -> list[dict[str, str]]:
        status = await self.check_ollama_status()
        if not status["running"]:
            return []
        recommended = {name: display for display, name, _ in _RECOMMENDED}
        available_models = status.get("available_models")
        if not isinstance(available_models, list):
            return []
        return [
            {
                "display_name": recommended.get(name, name),
                "model_name": name,
                "provider": "Ollama",
            }
            for name in available_models
            if isinstance(name, str)
        ]

    async def start_server(self) -> dict[str, object]:
        executable = shutil.which("ollama")
        if executable is None:
            return {"success": False, "message": "Ollama is not installed"}
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = await asyncio.to_thread(
            subprocess.Popen,
            [executable, "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        for _ in range(20):
            if (await self.check_ollama_status())["running"]:
                return {"success": True, "message": "Ollama server started successfully"}
            await asyncio.sleep(0.25)
        return {"success": False, "message": "Ollama server did not become ready"}

    async def stop_server(self) -> dict[str, object]:
        if self._process is None or self._process.poll() is not None:
            return {"success": False, "message": "Ollama server is not managed by this app"}
        self._process.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(self._process.wait), timeout=5)
        except TimeoutError:
            self._process.kill()
            await asyncio.to_thread(self._process.wait)
        self._process = None
        return {"success": True, "message": "Ollama server stopped successfully"}

    async def download_model(self, model_name: str) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                response = await client.post(
                    f"{self.base_url}/api/pull", json={"name": model_name, "stream": False}
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return {"success": False, "message": f"Failed to download model: {exc}"}
        return {"success": True, "message": f"Model {model_name} downloaded successfully"}

    async def download_model_with_progress(self, model_name: str) -> AsyncIterator[str]:
        self._cancelled.discard(model_name)
        self._progress[model_name] = {"status": "starting", "percentage": 0.0}
        try:
            async with (
                httpx.AsyncClient(timeout=None) as client,
                client.stream(
                    "POST",
                    f"{self.base_url}/api/pull",
                    json={"name": model_name, "stream": True},
                ) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if model_name in self._cancelled:
                        value: dict[str, Any] = {
                            "status": "cancelled",
                            "message": "Download cancelled",
                        }
                        self._progress[model_name] = value
                        yield f"data: {json.dumps(value, separators=(',', ':'))}\n\n"
                        return
                    if not line:
                        continue
                    payload = json.loads(line)
                    total = payload.get("total") or 0
                    completed = payload.get("completed") or 0
                    percentage = completed / total * 100 if total else None
                    status = "completed" if payload.get("status") == "success" else "downloading"
                    value = {
                        "status": status,
                        "percentage": percentage,
                        "message": payload.get("status"),
                        "bytes_downloaded": completed or None,
                        "total_bytes": total or None,
                    }
                    self._progress[model_name] = value
                    yield f"data: {json.dumps(value, separators=(',', ':'))}\n\n"
        except (httpx.HTTPError, ValueError) as exc:
            value = {"status": "error", "message": str(exc)}
            self._progress[model_name] = value
            yield f"data: {json.dumps(value, separators=(',', ':'))}\n\n"

    def get_download_progress(self, model_name: str) -> dict[str, Any] | None:
        return self._progress.get(model_name)

    def get_all_download_progress(self) -> dict[str, dict[str, Any]]:
        return dict(self._progress)

    def cancel_download(self, model_name: str) -> bool:
        if model_name not in self._progress:
            return False
        self._cancelled.add(model_name)
        return True

    async def delete_model(self, model_name: str) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    "DELETE", f"{self.base_url}/api/delete", json={"name": model_name}
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return {"success": False, "message": f"Failed to delete model: {exc}"}
        return {"success": True, "message": f"Model {model_name} deleted successfully"}

    async def get_recommended_models(self) -> list[dict[str, str]]:
        return [
            {"display_name": display, "model_name": name, "provider": provider}
            for display, name, provider in _RECOMMENDED
        ]


def _service(request: Request) -> Any:
    service = getattr(request.app.state, "compatibility_ollama", None)
    if service is None:
        raise HTTPException(503, "Ollama service unavailable")
    return service


def _model_name(value: str) -> str:
    if not _MODEL.fullmatch(value) or ".." in value:
        raise HTTPException(400, "Invalid model name")
    return value


async def _status(service: Any) -> dict[str, Any]:
    try:
        return dict(await service.check_ollama_status())
    except Exception as exc:
        raise HTTPException(500, f"Failed to check Ollama status: {exc}") from exc


@router.get("/status", response_model=OllamaStatusResponse)
async def status(request: Request) -> OllamaStatusResponse:
    return OllamaStatusResponse.model_validate(await _status(_service(request)))


@router.post("/start", response_model=ActionResponse)
async def start(request: Request) -> ActionResponse:
    service = _service(request)
    current = await _status(service)
    if not current.get("installed"):
        raise HTTPException(400, "Ollama is not installed on this system")
    if current.get("running"):
        return ActionResponse(success=True, message="Ollama server is already running")
    result = await service.start_server()
    if not result.get("success"):
        raise HTTPException(500, str(result.get("message", "Failed to start Ollama server")))
    return ActionResponse.model_validate(result)


@router.post("/stop", response_model=ActionResponse)
async def stop(request: Request) -> ActionResponse:
    service = _service(request)
    current = await _status(service)
    if not current.get("installed"):
        raise HTTPException(400, "Ollama is not installed on this system")
    if not current.get("running"):
        return ActionResponse(success=True, message="Ollama server is already stopped")
    result = await service.stop_server()
    if not result.get("success"):
        raise HTTPException(500, str(result.get("message", "Failed to stop Ollama server")))
    return ActionResponse.model_validate(result)


async def _require_running(service: Any) -> None:
    current = await _status(service)
    if not current.get("installed"):
        raise HTTPException(400, "Ollama is not installed on this system")
    if not current.get("running"):
        raise HTTPException(400, "Ollama server is not running. Please start it first.")


@router.post("/models/download", response_model=ActionResponse)
async def download(body: ModelRequest, request: Request) -> ActionResponse:
    name = _model_name(body.model_name)
    service = _service(request)
    await _require_running(service)
    result = await service.download_model(name)
    if not result.get("success"):
        raise HTTPException(500, str(result.get("message", "Failed to download model")))
    return ActionResponse.model_validate(result)


@router.post("/models/download/progress")
async def download_progress(body: ModelRequest, request: Request) -> StreamingResponse:
    name = _model_name(body.model_name)
    service = _service(request)
    await _require_running(service)
    return StreamingResponse(
        service.download_model_with_progress(name),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
        },
    )


@router.get("/models/download/progress/{model_name}", response_model=ProgressResponse)
async def progress(model_name: str, request: Request) -> ProgressResponse:
    name = _model_name(model_name)
    value = _service(request).get_download_progress(name)
    if value is None:
        raise HTTPException(404, f"No active download found for model: {name}")
    return ProgressResponse.model_validate(value)


@router.get("/models/downloads/active", response_model=dict[str, ProgressResponse])
async def active_downloads(request: Request) -> dict[str, ProgressResponse]:
    values = _service(request).get_all_download_progress()
    return {
        name: ProgressResponse.model_validate(value)
        for name, value in values.items()
        if value.get("status") in {"starting", "downloading"}
    }


@router.delete("/models/download/{model_name}", response_model=ActionResponse)
async def cancel_download(model_name: str, request: Request) -> ActionResponse:
    name = _model_name(model_name)
    if not _service(request).cancel_download(name):
        raise HTTPException(404, f"No active download found for model: {name}")
    return ActionResponse(success=True, message=f"Download cancelled for {name}")


@router.delete("/models/{model_name}", response_model=ActionResponse)
async def delete_model(model_name: str, request: Request) -> ActionResponse:
    name = _model_name(model_name)
    service = _service(request)
    await _require_running(service)
    result = await service.delete_model(name)
    if not result.get("success"):
        raise HTTPException(500, str(result.get("message", "Failed to delete model")))
    return ActionResponse.model_validate(result)


@router.get("/models/recommended", response_model=list[RecommendedModel])
async def recommended(request: Request) -> list[RecommendedModel]:
    return [
        RecommendedModel.model_validate(item)
        for item in await _service(request).get_recommended_models()
    ]
