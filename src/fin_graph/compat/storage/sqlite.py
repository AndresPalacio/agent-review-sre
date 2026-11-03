"""Standalone SQLAlchemy asyncio store for the clean local compatibility profile."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
    update,
)
from sqlalchemy.engine import URL, RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from fin_graph.compat.contracts import JsonObject, JsonValue
from fin_graph.compat.storage.crypto import SecretCipher
from fin_graph.compat.storage.models import (
    ArtifactRecord,
    BacktestJobRecord,
    FlowRecord,
    FlowRevision,
    FlowRunRecord,
    JobStatus,
    ProviderKeyRecord,
    RunCycleRecord,
    RunStatus,
)
from fin_graph.compat.storage.protocol import (
    CompatibilityStore,
    ConcurrentUpdateError,
    DuplicateNameError,
    RecordNotFoundError,
)

type _RecordRow = RowMapping | Mapping[str, Any]

_metadata = MetaData()

_flows = Table(
    "compat_flows",
    _metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False, unique=True),
    Column("description", Text, nullable=False),
    Column("active_revision", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("version", Integer, nullable=False),
)
_flow_revisions = Table(
    "compat_flow_revisions",
    _metadata,
    Column("id", String, primary_key=True),
    Column("flow_id", String, ForeignKey("compat_flows.id", ondelete="CASCADE"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("definition", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint("flow_id", "revision", name="uq_compat_flow_revision"),
)
_flow_runs = Table(
    "compat_flow_runs",
    _metadata,
    Column("id", String, primary_key=True),
    Column("flow_id", String, ForeignKey("compat_flows.id", ondelete="CASCADE"), nullable=False),
    Column("flow_revision", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("request", Text, nullable=False),
    Column("result", Text),
    Column("error", Text),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("version", Integer, nullable=False),
)
_run_cycles = Table(
    "compat_run_cycles",
    _metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, ForeignKey("compat_flow_runs.id", ondelete="CASCADE"), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("event", String, nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint("run_id", "sequence", name="uq_compat_run_cycle_sequence"),
    CheckConstraint("length(trim(event)) > 0", name="ck_compat_run_cycle_event"),
)
_provider_keys = Table(
    "compat_provider_keys",
    _metadata,
    Column("id", String, primary_key=True),
    Column("provider", String, nullable=False, unique=True),
    Column("encrypted_secret", Text, nullable=False),
    Column("key_hint", String, nullable=False),
    Column("is_active", Integer, nullable=False, default=1),
    Column("description", Text),
    Column("last_used", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("version", Integer, nullable=False),
)
_backtest_jobs = Table(
    "compat_backtest_jobs",
    _metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("status", String, nullable=False),
    Column("request", Text, nullable=False),
    Column("result", Text),
    Column("error", Text),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("version", Integer, nullable=False),
    CheckConstraint("length(trim(kind)) > 0", name="ck_compat_backtest_job_kind"),
)
_artifacts = Table(
    "compat_artifacts",
    _metadata,
    Column("id", String, primary_key=True),
    Column(
        "job_id",
        String,
        ForeignKey("compat_backtest_jobs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("kind", String, nullable=False),
    Column("path", Text, nullable=False),
    Column("metadata_json", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("version", Integer, nullable=False),
    CheckConstraint("length(trim(kind)) > 0", name="ck_compat_artifact_kind"),
    CheckConstraint("length(trim(path)) > 0", name="ck_compat_artifact_path"),
)


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def _json(value: JsonValue) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _load_json(value: str | None) -> JsonValue:
    return None if value is None else cast(JsonValue, json.loads(value))


def _id() -> str:
    return uuid.uuid4().hex


def _non_blank(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be blank")
    return normalized


def _flow(row: _RecordRow) -> FlowRecord:
    return FlowRecord.model_validate(row)


def _revision(row: _RecordRow) -> FlowRevision:
    values = dict(row)
    values["definition"] = _load_json(values["definition"])
    return FlowRevision.model_validate(values)


def _run(row: _RecordRow) -> FlowRunRecord:
    values = dict(row)
    values["request"] = _load_json(values["request"])
    values["result"] = _load_json(values["result"])
    return FlowRunRecord.model_validate(values)


def _cycle(row: _RecordRow) -> RunCycleRecord:
    values = dict(row)
    values["payload"] = _load_json(values["payload"])
    return RunCycleRecord.model_validate(values)


def _provider_key(row: _RecordRow) -> ProviderKeyRecord:
    public = {key: value for key, value in row.items() if key != "encrypted_secret"}
    return ProviderKeyRecord.model_validate(public)


def _job(row: _RecordRow) -> BacktestJobRecord:
    values = dict(row)
    values["request"] = _load_json(values["request"])
    values["result"] = _load_json(values["result"])
    return BacktestJobRecord.model_validate(values)


def _artifact(row: _RecordRow) -> ArtifactRecord:
    values = dict(row)
    values["metadata"] = _load_json(values.pop("metadata_json"))
    return ArtifactRecord.model_validate(values)


async def _mapping_or_none(connection: AsyncConnection, statement: Any) -> RowMapping | None:
    result = await connection.execute(statement)
    return result.mappings().first()


def _rowcount(result: Any) -> int:
    return int(getattr(result, "rowcount", -1))


class SqliteCompatibilityStore:
    """SQLAlchemy Core implementation with immutable revisions and version CAS."""

    def __init__(self, engine: AsyncEngine, cipher: SecretCipher) -> None:
        self._engine = engine
        self._cipher = cipher
        self._write_lock = asyncio.Lock()

    async def create_flow(
        self, name: str, definition: JsonObject, *, description: str = ""
    ) -> tuple[FlowRecord, FlowRevision]:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("flow name must not be blank")
        created = _now()
        flow_values = {
            "id": _id(),
            "name": normalized_name,
            "description": description,
            "active_revision": 1,
            "created_at": _timestamp(created),
            "updated_at": _timestamp(created),
            "version": 1,
        }
        revision_values = {
            "id": _id(),
            "flow_id": flow_values["id"],
            "revision": 1,
            "definition": _json(definition),
            "created_at": _timestamp(created),
            "updated_at": _timestamp(created),
            "version": 1,
        }
        try:
            async with self._engine.begin() as connection:
                await connection.execute(_flows.insert().values(**flow_values))
                await connection.execute(_flow_revisions.insert().values(**revision_values))
        except IntegrityError as exc:
            raise DuplicateNameError(f"flow name already exists: {normalized_name}") from exc
        return _flow(flow_values), _revision(revision_values)

    async def update_flow(
        self,
        flow_id: str,
        *,
        expected_version: int,
        definition: JsonObject,
        name: str | None = None,
        description: str | None = None,
    ) -> tuple[FlowRecord, FlowRevision]:
        try:
            async with self._engine.begin() as connection:
                current = await _mapping_or_none(
                    connection, select(_flows).where(_flows.c.id == flow_id)
                )
                if current is None:
                    raise RecordNotFoundError(f"flow not found: {flow_id}")
                next_revision = int(current["active_revision"]) + 1
                changed = _now()
                flow_values = {
                    "name": current["name"] if name is None else name.strip(),
                    "description": current["description"] if description is None else description,
                    "active_revision": next_revision,
                    "updated_at": _timestamp(changed),
                    "version": expected_version + 1,
                }
                if not flow_values["name"]:
                    raise ValueError("flow name must not be blank")
                result = await connection.execute(
                    update(_flows)
                    .where(_flows.c.id == flow_id, _flows.c.version == expected_version)
                    .values(**flow_values)
                )
                if _rowcount(result) != 1:
                    raise ConcurrentUpdateError(f"flow version changed: {flow_id}")
                revision_values = {
                    "id": _id(),
                    "flow_id": flow_id,
                    "revision": next_revision,
                    "definition": _json(definition),
                    "created_at": _timestamp(changed),
                    "updated_at": _timestamp(changed),
                    "version": 1,
                }
                await connection.execute(_flow_revisions.insert().values(**revision_values))
                stored = {**dict(current), **flow_values}
        except IntegrityError as exc:
            requested_name = name.strip() if name is not None else flow_id
            raise DuplicateNameError(f"flow name already exists: {requested_name}") from exc
        return _flow(stored), _revision(revision_values)

    async def get_flow(self, flow_id: str) -> FlowRecord | None:
        async with self._engine.connect() as connection:
            row = await _mapping_or_none(connection, select(_flows).where(_flows.c.id == flow_id))
        return None if row is None else _flow(row)

    async def list_flows(self) -> tuple[FlowRecord, ...]:
        async with self._engine.connect() as connection:
            rows = (
                await connection.execute(select(_flows).order_by(_flows.c.created_at, _flows.c.id))
            ).mappings()
            return tuple(_flow(row) for row in rows)

    async def delete_flow(self, flow_id: str, *, expected_version: int) -> bool:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                _flows.delete().where(
                    _flows.c.id == flow_id,
                    _flows.c.version == expected_version,
                )
            )
        return _rowcount(result) == 1

    async def get_flow_revision(self, flow_id: str, revision: int) -> FlowRevision | None:
        async with self._engine.connect() as connection:
            row = await _mapping_or_none(
                connection,
                select(_flow_revisions).where(
                    _flow_revisions.c.flow_id == flow_id,
                    _flow_revisions.c.revision == revision,
                ),
            )
        return None if row is None else _revision(row)

    async def list_flow_revisions(self, flow_id: str) -> tuple[FlowRevision, ...]:
        async with self._engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(_flow_revisions)
                    .where(_flow_revisions.c.flow_id == flow_id)
                    .order_by(_flow_revisions.c.revision)
                )
            ).mappings()
            return tuple(_revision(row) for row in rows)

    async def create_flow_run(
        self, flow_id: str, flow_revision: int, request: JsonObject
    ) -> FlowRunRecord:
        created = _now()
        values = {
            "id": _id(),
            "flow_id": flow_id,
            "flow_revision": flow_revision,
            "status": RunStatus.PENDING.value,
            "request": _json(request),
            "result": None,
            "error": None,
            "created_at": _timestamp(created),
            "updated_at": _timestamp(created),
            "version": 1,
        }
        async with self._engine.begin() as connection:
            revision = await _mapping_or_none(
                connection,
                select(_flow_revisions.c.id).where(
                    _flow_revisions.c.flow_id == flow_id,
                    _flow_revisions.c.revision == flow_revision,
                ),
            )
            if revision is None:
                raise RecordNotFoundError(f"flow revision not found: {flow_id}@{flow_revision}")
            await connection.execute(_flow_runs.insert().values(**values))
        return _run(values)

    async def get_flow_run(self, run_id: str) -> FlowRunRecord | None:
        async with self._engine.connect() as connection:
            row = await _mapping_or_none(
                connection, select(_flow_runs).where(_flow_runs.c.id == run_id)
            )
        return None if row is None else _run(row)

    async def list_flow_runs(self, flow_id: str | None = None) -> tuple[FlowRunRecord, ...]:
        statement = select(_flow_runs)
        if flow_id is not None:
            statement = statement.where(_flow_runs.c.flow_id == flow_id)
        statement = statement.order_by(_flow_runs.c.created_at, _flow_runs.c.id)
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings()
            return tuple(_run(row) for row in rows)

    async def delete_flow_run(self, run_id: str) -> bool:
        async with self._engine.begin() as connection:
            result = await connection.execute(_flow_runs.delete().where(_flow_runs.c.id == run_id))
        return _rowcount(result) == 1

    async def delete_flow_runs(self, flow_id: str) -> int:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                _flow_runs.delete().where(_flow_runs.c.flow_id == flow_id)
            )
        return max(_rowcount(result), 0)

    async def transition_flow_run(
        self,
        run_id: str,
        *,
        expected_version: int,
        status: RunStatus,
        result: JsonValue = None,
        error: str | None = None,
    ) -> FlowRunRecord | None:
        changed = _now()
        async with self._engine.begin() as connection:
            outcome = await connection.execute(
                update(_flow_runs)
                .where(_flow_runs.c.id == run_id, _flow_runs.c.version == expected_version)
                .values(
                    status=status.value,
                    result=None if result is None else _json(result),
                    error=error,
                    updated_at=_timestamp(changed),
                    version=expected_version + 1,
                )
            )
            if _rowcount(outcome) != 1:
                return None
            row = await _mapping_or_none(
                connection, select(_flow_runs).where(_flow_runs.c.id == run_id)
            )
        return None if row is None else _run(row)

    async def append_run_cycle(
        self, run_id: str, event: str, payload: JsonObject
    ) -> RunCycleRecord:
        normalized_event = _non_blank(event, "event")
        async with self._write_lock:
            created = _now()
            async with self._engine.begin() as connection:
                run = await _mapping_or_none(
                    connection, select(_flow_runs.c.id).where(_flow_runs.c.id == run_id)
                )
                if run is None:
                    raise RecordNotFoundError(f"flow run not found: {run_id}")
                sequence = (
                    int(
                        (
                            await connection.execute(
                                select(func.coalesce(func.max(_run_cycles.c.sequence), 0)).where(
                                    _run_cycles.c.run_id == run_id
                                )
                            )
                        ).scalar_one()
                    )
                    + 1
                )
                candidate = RunCycleRecord(
                    id=_id(),
                    run_id=run_id,
                    sequence=sequence,
                    event=normalized_event,
                    payload=payload,
                    created_at=created,
                    updated_at=created,
                    version=1,
                )
                values = {
                    **candidate.model_dump(exclude={"payload"}),
                    "payload": _json(candidate.payload),
                    "created_at": _timestamp(candidate.created_at),
                    "updated_at": _timestamp(candidate.updated_at),
                }
                await connection.execute(_run_cycles.insert().values(**values))
        return candidate

    async def list_run_cycles(self, run_id: str) -> tuple[RunCycleRecord, ...]:
        async with self._engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(_run_cycles)
                    .where(_run_cycles.c.run_id == run_id)
                    .order_by(_run_cycles.c.sequence)
                )
            ).mappings()
            return tuple(_cycle(row) for row in rows)

    async def put_provider_key(
        self,
        provider: str,
        secret: str,
        *,
        description: str | None = None,
        is_active: bool = True,
    ) -> ProviderKeyRecord:
        normalized = provider.strip().lower()
        if not normalized or not secret:
            raise ValueError("provider and secret must not be blank")
        encrypted = self._cipher.encrypt(secret)
        hint = f"…{secret[-4:]}" if len(secret) > 4 else "…"
        async with self._write_lock:
            changed = _now()
            async with self._engine.begin() as connection:
                current = await _mapping_or_none(
                    connection,
                    select(_provider_keys).where(_provider_keys.c.provider == normalized),
                )
                if current is None:
                    values = {
                        "id": _id(),
                        "provider": normalized,
                        "encrypted_secret": encrypted,
                        "key_hint": hint,
                        "is_active": int(is_active),
                        "description": description,
                        "last_used": None,
                        "created_at": _timestamp(changed),
                        "updated_at": _timestamp(changed),
                        "version": 1,
                    }
                    await connection.execute(_provider_keys.insert().values(**values))
                else:
                    values = {
                        **dict(current),
                        "encrypted_secret": encrypted,
                        "key_hint": hint,
                        "is_active": int(is_active),
                        "description": description,
                        "updated_at": _timestamp(changed),
                        "version": int(current["version"]) + 1,
                    }
                    await connection.execute(
                        update(_provider_keys)
                        .where(_provider_keys.c.id == current["id"])
                        .values(
                            encrypted_secret=encrypted,
                            key_hint=hint,
                            is_active=int(is_active),
                            description=description,
                            updated_at=values["updated_at"],
                            version=values["version"],
                        )
                    )
        return _provider_key(values)

    async def list_provider_keys(self) -> tuple[ProviderKeyRecord, ...]:
        statement = select(
            _provider_keys.c.id,
            _provider_keys.c.provider,
            _provider_keys.c.key_hint,
            _provider_keys.c.is_active,
            _provider_keys.c.description,
            _provider_keys.c.last_used,
            _provider_keys.c.created_at,
            _provider_keys.c.updated_at,
            _provider_keys.c.version,
        ).order_by(_provider_keys.c.provider)
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings()
            return tuple(_provider_key(row) for row in rows)

    async def get_provider_secret(self, provider: str) -> str | None:
        async with self._engine.connect() as connection:
            row = await _mapping_or_none(
                connection,
                select(_provider_keys.c.encrypted_secret).where(
                    _provider_keys.c.provider == provider.strip().lower()
                ),
            )
        return None if row is None else self._cipher.decrypt(str(row["encrypted_secret"]))

    async def update_provider_key(
        self,
        provider: str,
        *,
        expected_version: int,
        secret: str | None,
        description: str | None,
        is_active: bool,
        last_used: datetime | None,
    ) -> ProviderKeyRecord | None:
        normalized = provider.strip().lower()
        if not normalized:
            raise ValueError("provider must not be blank")
        changed = _now()
        async with self._write_lock, self._engine.begin() as connection:
            current = await _mapping_or_none(
                connection,
                select(_provider_keys).where(
                    _provider_keys.c.provider == normalized,
                    _provider_keys.c.version == expected_version,
                ),
            )
            if current is None:
                return None
            values: dict[str, Any] = {
                "description": description,
                "is_active": int(is_active),
                "last_used": _timestamp(last_used) if last_used is not None else None,
                "updated_at": _timestamp(changed),
                "version": expected_version + 1,
            }
            if secret is not None:
                if not secret:
                    raise ValueError("secret must not be blank")
                values["encrypted_secret"] = self._cipher.encrypt(secret)
                values["key_hint"] = f"…{secret[-4:]}" if len(secret) > 4 else "…"
            outcome = await connection.execute(
                update(_provider_keys)
                .where(
                    _provider_keys.c.provider == normalized,
                    _provider_keys.c.version == expected_version,
                )
                .values(**values)
            )
            if _rowcount(outcome) != 1:
                return None
            row = await _mapping_or_none(
                connection,
                select(_provider_keys).where(_provider_keys.c.provider == normalized),
            )
        return None if row is None else _provider_key(row)

    async def delete_provider_key(self, provider: str, *, expected_version: int) -> bool:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                _provider_keys.delete().where(
                    _provider_keys.c.provider == provider.strip().lower(),
                    _provider_keys.c.version == expected_version,
                )
            )
        return _rowcount(result) == 1

    async def create_backtest_job(self, kind: str, request: JsonObject) -> BacktestJobRecord:
        created = _now()
        candidate = BacktestJobRecord(
            id=_id(),
            kind=_non_blank(kind, "kind"),
            status=JobStatus.QUEUED,
            request=request,
            result=None,
            error=None,
            created_at=created,
            updated_at=created,
            version=1,
        )
        values = {
            **candidate.model_dump(exclude={"request", "result"}, mode="json"),
            "request": _json(candidate.request),
            "result": None,
            "created_at": _timestamp(created),
            "updated_at": _timestamp(created),
        }
        async with self._engine.begin() as connection:
            await connection.execute(_backtest_jobs.insert().values(**values))
        return candidate

    async def get_backtest_job(self, job_id: str) -> BacktestJobRecord | None:
        async with self._engine.connect() as connection:
            row = await _mapping_or_none(
                connection, select(_backtest_jobs).where(_backtest_jobs.c.id == job_id)
            )
        return None if row is None else _job(row)

    async def list_backtest_jobs(self) -> tuple[BacktestJobRecord, ...]:
        async with self._engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(_backtest_jobs).order_by(
                        _backtest_jobs.c.created_at, _backtest_jobs.c.id
                    )
                )
            ).mappings()
            return tuple(_job(row) for row in rows)

    async def transition_backtest_job(
        self,
        job_id: str,
        *,
        expected_version: int,
        status: JobStatus,
        result: JsonValue = None,
        error: str | None = None,
    ) -> BacktestJobRecord | None:
        changed = _now()
        async with self._engine.begin() as connection:
            outcome = await connection.execute(
                update(_backtest_jobs)
                .where(
                    _backtest_jobs.c.id == job_id,
                    _backtest_jobs.c.version == expected_version,
                )
                .values(
                    status=status.value,
                    result=None if result is None else _json(result),
                    error=error,
                    updated_at=_timestamp(changed),
                    version=expected_version + 1,
                )
            )
            if _rowcount(outcome) != 1:
                return None
            row = await _mapping_or_none(
                connection, select(_backtest_jobs).where(_backtest_jobs.c.id == job_id)
            )
        return None if row is None else _job(row)

    async def create_artifact(
        self,
        job_id: str,
        kind: str,
        path: str,
        metadata: JsonObject,
    ) -> ArtifactRecord:
        created = _now()
        candidate = ArtifactRecord(
            id=_id(),
            job_id=job_id,
            kind=_non_blank(kind, "kind"),
            path=_non_blank(path, "path"),
            metadata=metadata,
            created_at=created,
            updated_at=created,
            version=1,
        )
        values = {
            **candidate.model_dump(exclude={"metadata"}),
            "metadata_json": _json(candidate.metadata),
            "created_at": _timestamp(created),
            "updated_at": _timestamp(created),
        }
        try:
            async with self._engine.begin() as connection:
                await connection.execute(_artifacts.insert().values(**values))
        except IntegrityError as exc:
            raise RecordNotFoundError(f"backtest job not found: {job_id}") from exc
        return candidate

    async def list_artifacts(self, job_id: str | None = None) -> tuple[ArtifactRecord, ...]:
        statement = select(_artifacts)
        if job_id is not None:
            statement = statement.where(_artifacts.c.job_id == job_id)
        statement = statement.order_by(_artifacts.c.created_at, _artifacts.c.id)
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings()
            return tuple(_artifact(row) for row in rows)


@asynccontextmanager
async def _open_compat_sqlite(
    path: Path, cipher: SecretCipher
) -> AsyncIterator[CompatibilityStore]:
    engine = create_async_engine(
        URL.create("sqlite+aiosqlite", database=str(path)),
        connect_args={"timeout": 30},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    try:
        async with engine.begin() as connection:
            await connection.run_sync(_metadata.create_all)
            columns = {
                str(row._mapping["name"])
                for row in (
                    await connection.exec_driver_sql("PRAGMA table_info(compat_provider_keys)")
                )
            }
            migrations = {
                "is_active": (
                    "ALTER TABLE compat_provider_keys ADD COLUMN is_active "
                    "INTEGER NOT NULL DEFAULT 1"
                ),
                "description": "ALTER TABLE compat_provider_keys ADD COLUMN description TEXT",
                "last_used": "ALTER TABLE compat_provider_keys ADD COLUMN last_used TEXT",
            }
            for name, statement in migrations.items():
                if name not in columns:
                    await connection.exec_driver_sql(statement)
        yield SqliteCompatibilityStore(engine, cipher)
    finally:
        await engine.dispose()


def open_compat_sqlite(
    path: Path, cipher: SecretCipher
) -> AbstractAsyncContextManager[CompatibilityStore]:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return _open_compat_sqlite(resolved, cipher)
