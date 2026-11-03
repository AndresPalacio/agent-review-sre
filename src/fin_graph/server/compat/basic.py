"""Reference root, liveness, ping stream, and confined JSON export routes."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from fin_graph.compat.ai_hedge_fund.schemas import SaveJsonRequest

router = APIRouter()
root_router = APIRouter()
health_router = APIRouter()


@root_router.get("/")
async def root() -> dict[str, str]:
    return {"message": "Welcome to AI Hedge Fund API"}


@health_router.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "ai-hedge-fund",
        "version": "0.1.0",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/ping")
async def ping() -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        for index in range(5):
            data = {"ping": f"ping {index + 1}/5", "timestamp": index + 1}
            yield f"data: {json.dumps(data)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream")


def _export_target(root: Path, filename: str) -> Path:
    if not filename or "\x00" in filename or "\\" in filename:
        raise ValueError("Invalid filename")
    posix, windows = PurePosixPath(filename), PureWindowsPath(filename)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise ValueError("Invalid filename")
    target = (root / Path(*posix.parts)).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Invalid filename")
    return target


def _atomic_json(target: Path, data: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


@router.post("/storage/save-json")
def save_json(body: SaveJsonRequest, request: Request) -> dict[str, object]:
    root = Path(request.app.state.compatibility_export_dir).expanduser().resolve()
    try:
        target = _export_target(root, body.filename)
        _atomic_json(target, body.data)
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "success": True,
        "message": f"File saved successfully to {target}",
        "filename": body.filename,
    }
