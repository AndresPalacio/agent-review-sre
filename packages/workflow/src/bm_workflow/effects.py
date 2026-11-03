"""Conservative effect ledger: ambiguous sends are never automatically repeated.

The ledger and remote operation do not share a transaction. A crash between a
send and confirmation leaves PENDING; the host must reconcile that outcome.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class EffectError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class EffectState(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


class EffectRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    key: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    state: EffectState = EffectState.PENDING
    version: int = Field(default=0, ge=0)
    result: JsonValue = None

    @model_validator(mode="after")
    def valid_result(self) -> EffectRecord:
        if self.state != EffectState.CONFIRMED and self.result is not None:
            raise ValueError("unconfirmed_result")
        return self


class EffectStore(Protocol):
    def create(self, namespace: str, record: EffectRecord) -> bool: ...

    def get(self, namespace: str, key: str) -> EffectRecord | None: ...

    def compare_and_swap(
        self, namespace: str, key: str, expected_version: int, record: EffectRecord
    ) -> bool: ...


def _namespace(namespace: str) -> None:
    if not isinstance(namespace, str) or not namespace:
        raise EffectError("invalid_namespace")


def _json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _snapshot(record: EffectRecord) -> EffectRecord:
    try:
        return EffectRecord.model_validate_json(_json(record.model_dump(mode="json")))
    except (TypeError, ValueError):
        raise EffectError("invalid_effect_record") from None


def _transition(current: EffectRecord, candidate: EffectRecord, expected: int) -> None:
    if (
        current.state != EffectState.PENDING
        or candidate.state not in {EffectState.CONFIRMED, EffectState.UNKNOWN}
        or candidate.version != expected + 1
        or candidate.key != current.key
        or candidate.fingerprint != current.fingerprint
    ):
        raise EffectError("invalid_effect_transition")


class InMemoryEffectStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], EffectRecord] = {}
        self._lock = RLock()

    def create(self, namespace: str, record: EffectRecord) -> bool:
        _namespace(namespace)
        candidate = _snapshot(record)
        if candidate.state != EffectState.PENDING or candidate.version != 0:
            raise EffectError("invalid_initial_effect")
        with self._lock:
            if (namespace, record.key) in self._records:
                return False
            self._records[namespace, record.key] = candidate
            return True

    def get(self, namespace: str, key: str) -> EffectRecord | None:
        _namespace(namespace)
        with self._lock:
            value = self._records.get((namespace, key))
            return _snapshot(value) if value is not None else None

    def compare_and_swap(
        self, namespace: str, key: str, expected_version: int, record: EffectRecord
    ) -> bool:
        _namespace(namespace)
        candidate = _snapshot(record)
        with self._lock:
            current = self._records.get((namespace, key))
            if current is None or current.version != expected_version:
                return False
            _transition(current, candidate, expected_version)
            self._records[namespace, key] = candidate
            return True


class SQLiteEffectStore:
    def __init__(self, path: str | Path) -> None:
        self._lock = RLock()
        connection = None
        try:
            connection = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS workflow_effects (namespace TEXT NOT NULL, "
                "effect_key TEXT NOT NULL, version INTEGER NOT NULL, document TEXT NOT NULL, "
                "PRIMARY KEY(namespace,effect_key))"
            )
            connection.commit()
            self._connection = connection
        except sqlite3.Error:
            if connection is not None:
                connection.close()
            raise EffectError("effect_store_unavailable") from None

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SQLiteEffectStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def create(self, namespace: str, record: EffectRecord) -> bool:
        _namespace(namespace)
        candidate = _snapshot(record)
        if candidate.state != EffectState.PENDING or candidate.version != 0:
            raise EffectError("invalid_initial_effect")
        try:
            with self._lock, self._connection:
                cursor = self._connection.execute(
                    "INSERT OR IGNORE INTO workflow_effects VALUES (?, ?, ?, ?)",
                    (namespace, candidate.key, candidate.version, candidate.model_dump_json()),
                )
                return cursor.rowcount == 1
        except sqlite3.Error:
            raise EffectError("effect_store_unavailable") from None

    def get(self, namespace: str, key: str) -> EffectRecord | None:
        _namespace(namespace)
        try:
            with self._lock:
                row = self._connection.execute(
                    "SELECT document FROM workflow_effects WHERE namespace=? AND effect_key=?",
                    (namespace, key),
                ).fetchone()
                return EffectRecord.model_validate_json(row[0]) if row else None
        except sqlite3.Error:
            raise EffectError("effect_store_unavailable") from None

    def compare_and_swap(
        self, namespace: str, key: str, expected_version: int, record: EffectRecord
    ) -> bool:
        _namespace(namespace)
        candidate = _snapshot(record)
        try:
            with self._lock, self._connection:
                self._connection.execute("BEGIN IMMEDIATE")
                current = self.get(namespace, key)
                if current is None or current.version != expected_version:
                    return False
                _transition(current, candidate, expected_version)
                cursor = self._connection.execute(
                    "UPDATE workflow_effects SET version=?, document=? "
                    "WHERE namespace=? AND effect_key=? AND version=?",
                    (
                        candidate.version,
                        candidate.model_dump_json(),
                        namespace,
                        key,
                        expected_version,
                    ),
                )
                return cursor.rowcount == 1
        except sqlite3.Error:
            raise EffectError("effect_store_unavailable") from None


class EffectCoordinator:
    def __init__(self, store: EffectStore, namespace: str = "default") -> None:
        _namespace(namespace)
        self.store = store
        self.namespace = namespace

    def execute(self, key: str, handler: Callable[[Any], Any], payload: Any) -> Any:
        try:
            encoded = _json(payload)
            intended = EffectRecord(
                key=key, fingerprint=hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            )
        except (TypeError, ValueError):
            raise EffectError("effect_invalid_payload") from None
        if not self.store.create(self.namespace, intended):
            current = self.store.get(self.namespace, key)
            if current is None:
                raise EffectError("effect_store_unavailable")
            if current.fingerprint != intended.fingerprint:
                raise EffectError("effect_payload_conflict")
            if current.state == EffectState.CONFIRMED:
                return json.loads(_json(current.result))
            raise EffectError(
                "effect_pending"
                if current.state == EffectState.PENDING
                else "effect_outcome_unknown"
            )
        try:
            result = json.loads(_json(handler(json.loads(encoded))))
            confirmed = _snapshot(
                intended.model_copy(
                    update={"state": EffectState.CONFIRMED, "version": 1, "result": result}
                )
            )
            if not self.store.compare_and_swap(self.namespace, key, 0, confirmed):
                raise EffectError("effect_outcome_unknown")
            return result
        except BaseException as error:
            # PENDING still prevents automatic replay if this write fails.
            with suppress(Exception):
                self.store.compare_and_swap(
                    self.namespace,
                    key,
                    0,
                    intended.model_copy(update={"state": EffectState.UNKNOWN, "version": 1}),
                )
            if not isinstance(error, Exception):
                raise
            raise EffectError("effect_outcome_unknown") from None
