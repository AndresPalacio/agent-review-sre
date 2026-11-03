"""Versioned run admission and execution; records are not graph checkpoints.

A worker lost after claiming RUNNING requires host reconciliation. This module
deliberately never retries it. Cancellation is durable only before execution.
"""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from dataclasses import asdict
from enum import StrEnum
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class PersistenceError(RuntimeError):
    """An error code safe for a transport boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class RunState(StrEnum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class RunRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    run_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    workflow_version: str = Field(min_length=1)
    definition_digest: str = Field(min_length=1)
    artifact_revision: str = Field(min_length=1)
    policy_version: str = Field(default="1", min_length=1)
    input_data: dict[str, JsonValue]
    state: RunState = RunState.ACCEPTED
    version: int = Field(default=0, ge=0)
    output_data: dict[str, JsonValue] | None = None
    cause: dict[str, str | None] | None = None

    @model_validator(mode="after")
    def valid_state(self) -> RunRecord:
        if self.state == RunState.SUCCEEDED:
            if self.output_data is None or self.cause is not None:
                raise ValueError("invalid_success_record")
        elif self.output_data is not None:
            raise ValueError("output_requires_success")
        if self.state in {RunState.ACCEPTED, RunState.RUNNING} and self.cause is not None:
            raise ValueError("active_record_has_cause")
        return self


class RunStore(Protocol):
    """Implementations must atomically create/CAS within each namespace."""

    def create(self, namespace: str, record: RunRecord) -> bool: ...

    def get(self, namespace: str, run_id: str) -> RunRecord | None: ...

    def compare_and_swap(
        self, namespace: str, run_id: str, expected_version: int, record: RunRecord
    ) -> bool: ...


def _namespace(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise PersistenceError("invalid_namespace")


def _snapshot(record: RunRecord) -> RunRecord:
    try:
        encoded = json.dumps(record.model_dump(mode="json"), allow_nan=False)
        return RunRecord.model_validate_json(encoded)
    except (TypeError, ValueError):
        raise PersistenceError("invalid_record") from None


def _transition(current: RunRecord, candidate: RunRecord, expected: int) -> None:
    identity = {
        "run_id",
        "name",
        "workflow_version",
        "definition_digest",
        "artifact_revision",
        "policy_version",
        "input_data",
    }
    legal = {
        RunState.ACCEPTED: {RunState.RUNNING, RunState.CANCELLED},
        RunState.RUNNING: {
            RunState.SUCCEEDED,
            RunState.FAILED,
            RunState.TIMED_OUT,
            RunState.CANCELLED,
        },
    }
    if (
        candidate.version != expected + 1
        or any(getattr(current, key) != getattr(candidate, key) for key in identity)
        or candidate.state not in legal.get(current.state, set())
    ):
        raise PersistenceError("invalid_transition")


class InMemoryRunStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], RunRecord] = {}
        self._lock = RLock()

    def create(self, namespace: str, record: RunRecord) -> bool:
        _namespace(namespace)
        candidate = _snapshot(record)
        if candidate.state != RunState.ACCEPTED or candidate.version != 0:
            raise PersistenceError("invalid_initial_record")
        with self._lock:
            key = namespace, record.run_id
            if key in self._records:
                return False
            self._records[key] = candidate
            return True

    def get(self, namespace: str, run_id: str) -> RunRecord | None:
        _namespace(namespace)
        with self._lock:
            value = self._records.get((namespace, run_id))
            return _snapshot(value) if value is not None else None

    def compare_and_swap(
        self, namespace: str, run_id: str, expected_version: int, record: RunRecord
    ) -> bool:
        _namespace(namespace)
        candidate = _snapshot(record)
        with self._lock:
            current = self._records.get((namespace, run_id))
            if current is None or current.version != expected_version:
                return False
            _transition(current, candidate, expected_version)
            self._records[namespace, run_id] = candidate
            return True


class SQLiteRunStore:
    """Local durable store; separate connections share SQLite's atomic writer lock."""

    def __init__(self, path: str | Path) -> None:
        self._lock = RLock()
        connection = None
        try:
            connection = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS workflow_runs ("
                "namespace TEXT NOT NULL, run_id TEXT NOT NULL, version INTEGER NOT NULL, "
                "document TEXT NOT NULL, PRIMARY KEY(namespace, run_id))"
            )
            connection.commit()
            self._connection = connection
        except sqlite3.Error:
            if connection is not None:
                connection.close()
            raise PersistenceError("store_unavailable") from None

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SQLiteRunStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def create(self, namespace: str, record: RunRecord) -> bool:
        _namespace(namespace)
        candidate = _snapshot(record)
        if candidate.state != RunState.ACCEPTED or candidate.version != 0:
            raise PersistenceError("invalid_initial_record")
        try:
            with self._lock, self._connection:
                cursor = self._connection.execute(
                    "INSERT OR IGNORE INTO workflow_runs VALUES (?, ?, ?, ?)",
                    (namespace, candidate.run_id, candidate.version, candidate.model_dump_json()),
                )
                return cursor.rowcount == 1
        except sqlite3.Error:
            raise PersistenceError("store_unavailable") from None

    def get(self, namespace: str, run_id: str) -> RunRecord | None:
        _namespace(namespace)
        try:
            with self._lock:
                row = self._connection.execute(
                    "SELECT document FROM workflow_runs WHERE namespace=? AND run_id=?",
                    (namespace, run_id),
                ).fetchone()
                return RunRecord.model_validate_json(row[0]) if row else None
        except sqlite3.Error:
            raise PersistenceError("store_unavailable") from None

    def compare_and_swap(
        self, namespace: str, run_id: str, expected_version: int, record: RunRecord
    ) -> bool:
        _namespace(namespace)
        candidate = _snapshot(record)
        try:
            with self._lock, self._connection:
                self._connection.execute("BEGIN IMMEDIATE")
                current = self.get(namespace, run_id)
                if current is None or current.version != expected_version:
                    return False
                _transition(current, candidate, expected_version)
                cursor = self._connection.execute(
                    "UPDATE workflow_runs SET version=?, document=? "
                    "WHERE namespace=? AND run_id=? AND version=?",
                    (
                        candidate.version,
                        candidate.model_dump_json(),
                        namespace,
                        run_id,
                        expected_version,
                    ),
                )
                return cursor.rowcount == 1
        except sqlite3.Error:
            raise PersistenceError("store_unavailable") from None


class RunService:
    def __init__(
        self, registry: Any, store: RunStore, artifact_revision: str, namespace: str = "default"
    ) -> None:
        _namespace(namespace)
        if not artifact_revision:
            raise PersistenceError("invalid_artifact_revision")
        self.registry = registry
        self.store = store
        self.artifact_revision = artifact_revision
        self.namespace = namespace

    def get(self, run_id: str) -> RunRecord | None:
        return self.store.get(self.namespace, run_id)

    def _get(self, run_id: str) -> RunRecord:
        record = self.get(run_id)
        if record is None:
            raise PersistenceError("run_not_found")
        return record

    def admit(
        self,
        *,
        name: str,
        version: str,
        input_data: Any,
        artifact_revision: str | None = None,
        policy_version: str = "1",
        run_id: str | None = None,
    ) -> RunRecord:
        from .definition import validate_model

        if artifact_revision is not None and artifact_revision != self.artifact_revision:
            raise PersistenceError("artifact_revision_mismatch")
        compiled = self.registry.resolve(name, version)
        try:
            validated = validate_model(compiled.definition.input_schema, input_data)
            record = RunRecord(
                run_id=run_id if run_id is not None else str(uuid4()),
                name=name,
                workflow_version=version,
                definition_digest=compiled.definition_digest,
                artifact_revision=self.artifact_revision,
                policy_version=policy_version,
                input_data=validated.model_dump(mode="json", round_trip=True),
            )
            record = _snapshot(record)
        except (TypeError, ValueError):
            raise PersistenceError("invalid_input") from None
        if self.store.create(self.namespace, record):
            return record
        existing = self._get(record.run_id)
        identity = (
            "name",
            "workflow_version",
            "definition_digest",
            "artifact_revision",
            "policy_version",
            "input_data",
        )
        if any(getattr(existing, key) != getattr(record, key) for key in identity):
            raise PersistenceError("run_conflict")
        return existing

    def cancel(self, run_id: str, *, expected_version: int) -> RunRecord:
        """Cancel ACCEPTED runs. RUNNING cancellation needs host coordination."""
        current = self._get(run_id)
        if current.state != RunState.ACCEPTED:
            raise PersistenceError("run_not_accepted")
        candidate = current.model_copy(
            update={
                "state": RunState.CANCELLED,
                "version": expected_version + 1,
                "cause": {"code": "operation_cancelled"},
            }
        )
        if not self.store.compare_and_swap(self.namespace, run_id, expected_version, candidate):
            raise PersistenceError("run_version_conflict")
        return candidate

    def _claim(self, run_id: str, expected_version: int) -> tuple[RunRecord, Any]:
        current = self._get(run_id)
        if current.state != RunState.ACCEPTED:
            raise PersistenceError("run_not_accepted")
        if current.artifact_revision != self.artifact_revision:
            raise PersistenceError("artifact_revision_mismatch")
        compiled = self.registry.resolve(current.name, current.workflow_version)
        if compiled.definition_digest != current.definition_digest:
            raise PersistenceError("definition_digest_mismatch")
        running = current.model_copy(
            update={"state": RunState.RUNNING, "version": expected_version + 1}
        )
        if not self.store.compare_and_swap(self.namespace, run_id, expected_version, running):
            raise PersistenceError("run_version_conflict")
        return running, compiled

    @staticmethod
    def _context(record: RunRecord, timeout_seconds: float | None) -> Any:
        from .runtime import RunContext

        return RunContext(
            name=record.name,
            version=record.workflow_version,
            definition_digest=record.definition_digest,
            run_id=record.run_id,
            deadline=monotonic() + timeout_seconds if timeout_seconds is not None else None,
        )

    def _finish(
        self, running: RunRecord, compiled: Any, result: Any = None, cause: str | None = None
    ) -> RunRecord:
        from .definition import validate_model
        from .runtime import SafeCause

        state = RunState.FAILED
        output = None
        safe_cause: dict[str, str | None] | None = {"code": cause or "execution_failed"}
        if result is not None:
            state = {
                "complete": RunState.SUCCEEDED,
                "failed": RunState.FAILED,
                "timed_out": RunState.TIMED_OUT,
                "cancelled": RunState.CANCELLED,
            }.get(str(result.status), RunState.FAILED)
            if state == RunState.SUCCEEDED:
                try:
                    output = validate_model(
                        compiled.definition.output_schema, result.output
                    ).model_dump(mode="json", round_trip=True)
                    output = json.loads(json.dumps(output, allow_nan=False))
                    safe_cause = None
                except (TypeError, ValueError):
                    state = RunState.FAILED
                    output = None
                    safe_cause = {"code": "invalid_output"}
            elif result.cause is not None:
                safe_cause = (
                    asdict(result.cause)
                    if isinstance(result.cause, SafeCause)
                    else {"code": "execution_failed"}
                )
        terminal = _snapshot(
            running.model_copy(
                update={
                    "state": state,
                    "version": running.version + 1,
                    "output_data": output,
                    "cause": safe_cause,
                }
            )
        )
        if not self.store.compare_and_swap(
            self.namespace, running.run_id, running.version, terminal
        ):
            raise PersistenceError("terminal_write_conflict")
        return terminal

    def execute(
        self, run_id: str, *, expected_version: int, timeout_seconds: float | None = None
    ) -> RunRecord:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("Use the async API (aexecute) inside a running event loop")
        self._validate_timeout(timeout_seconds)
        running, compiled = self._claim(run_id, expected_version)
        try:
            result = self.registry.run(
                name=running.name,
                version=running.workflow_version,
                input_data=self._rehydrate_input(compiled, running),
                context=self._context(running, timeout_seconds),
            )
        except Exception:
            return self._finish(running, compiled, cause="execution_failed")
        return self._finish(running, compiled, result)

    async def aexecute(
        self, run_id: str, *, expected_version: int, timeout_seconds: float | None = None
    ) -> RunRecord:
        self._validate_timeout(timeout_seconds)
        running, compiled = self._claim(run_id, expected_version)
        try:
            result = await self.registry.arun(
                name=running.name,
                version=running.workflow_version,
                input_data=self._rehydrate_input(compiled, running),
                context=self._context(running, timeout_seconds),
            )
        except asyncio.CancelledError:
            self._finish(running, compiled, SimpleResultCancelled())
            raise
        except Exception:
            return self._finish(running, compiled, cause="execution_failed")
        return self._finish(running, compiled, result)

    @staticmethod
    def _rehydrate_input(compiled: Any, record: RunRecord) -> dict[str, Any]:
        schema: type[BaseModel] = compiled.definition.input_schema
        validated = schema.model_validate_json(json.dumps(record.input_data), by_name=True)
        return validated.model_dump(mode="python", round_trip=True)

    @staticmethod
    def _validate_timeout(timeout_seconds: float | None) -> None:
        if timeout_seconds is not None and (
            not math.isfinite(timeout_seconds) or timeout_seconds < 0
        ):
            raise PersistenceError("invalid_timeout")


class SimpleResultCancelled:
    status = "cancelled"
    cause = None
