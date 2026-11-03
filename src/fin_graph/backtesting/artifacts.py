"""Crash-safe artifacts and replay journals for durable backtest jobs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from fin_graph.backtesting.models import BacktestEvent
from fin_graph.conformance.contracts import JsonObject


class BacktestRunOutput(BaseModel):
    """Transport-neutral output returned by a registered job runner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: JsonObject
    signals: tuple[JsonObject, ...] = ()


class SignalPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    items: tuple[JsonObject, ...]
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=2_000)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class PublishedArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    path: str
    sha256: str
    bytes: int = Field(ge=0)


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class BacktestArtifactStore:
    """Own job artifacts below one root and atomically publish success bundles."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._success_root = self.root / "completed"
        self._journal_root = self.root / "journals"
        self._success_root.mkdir(parents=True, exist_ok=True)
        self._journal_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_job_id(job_id: str) -> str:
        if not job_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in job_id
        ):
            raise ValueError("invalid job id")
        return job_id

    def success_directory(self, job_id: str) -> Path:
        return self._success_root / self._safe_job_id(job_id)

    def _journal_path(self, job_id: str) -> Path:
        return self._journal_root / f"{self._safe_job_id(job_id)}.json"

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def append_event(self, job_id: str, event: BacktestEvent) -> None:
        events = list(self.load_events(job_id))
        expected = events[-1].sequence + 1 if events else 0
        if event.sequence != expected:
            raise ValueError(f"event sequence must be {expected}, got {event.sequence}")
        events.append(event)
        self._atomic_write(
            self._journal_path(job_id),
            _json_bytes([item.model_dump(mode="json") for item in events]),
        )

    def load_events(self, job_id: str) -> tuple[BacktestEvent, ...]:
        path = self._journal_path(job_id)
        if not path.exists():
            return ()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("invalid backtest event journal")
        return tuple(BacktestEvent.model_validate(item) for item in raw)

    def publish_success(
        self,
        job_id: str,
        output: BacktestRunOutput,
    ) -> tuple[PublishedArtifact, ...]:
        target = self.success_directory(job_id)
        if target.exists():
            raise FileExistsError(f"success artifacts already exist for job {job_id}")
        staging = self._success_root / f".{job_id}.{uuid.uuid4().hex}.tmp"
        staging.mkdir(parents=False)
        try:
            payloads = {
                "result": _json_bytes(output.result),
                "signals": _json_bytes(list(output.signals)),
            }
            for kind, payload in payloads.items():
                (staging / f"{kind}.json").write_bytes(payload)
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return tuple(
            PublishedArtifact(
                kind=kind,
                path=str((target / f"{kind}.json").resolve()),
                sha256=hashlib.sha256(payload).hexdigest(),
                bytes=len(payload),
            )
            for kind, payload in payloads.items()
        )

    def discard_success(self, job_id: str) -> None:
        target = self.success_directory(job_id)
        if target.exists():
            shutil.rmtree(target)

    def load_signals(self, job_id: str) -> tuple[JsonObject, ...]:
        path = self.success_directory(job_id) / "signals.json"
        if not path.exists():
            return ()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
            raise ValueError("invalid signals artifact")
        return tuple(raw)
