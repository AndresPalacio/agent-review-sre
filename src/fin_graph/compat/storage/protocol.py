"""Storage port shared by the SQLite and Postgres compatibility backends."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from fin_graph.compat.contracts import JsonObject, JsonValue
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


class DuplicateNameError(ValueError):
    """A public unique name already exists."""


class RecordNotFoundError(LookupError):
    """A requested record or immutable revision does not exist."""


class ConcurrentUpdateError(RuntimeError):
    """The expected version lost a compare-and-swap race."""


@runtime_checkable
class CompatibilityStore(Protocol):
    async def create_flow(
        self, name: str, definition: JsonObject, *, description: str = ""
    ) -> tuple[FlowRecord, FlowRevision]: ...

    async def update_flow(
        self,
        flow_id: str,
        *,
        expected_version: int,
        definition: JsonObject,
        name: str | None = None,
        description: str | None = None,
    ) -> tuple[FlowRecord, FlowRevision]: ...

    async def get_flow(self, flow_id: str) -> FlowRecord | None: ...

    async def list_flows(self) -> tuple[FlowRecord, ...]: ...

    async def get_flow_revision(self, flow_id: str, revision: int) -> FlowRevision | None: ...

    async def list_flow_revisions(self, flow_id: str) -> tuple[FlowRevision, ...]: ...

    async def create_flow_run(
        self, flow_id: str, flow_revision: int, request: JsonObject
    ) -> FlowRunRecord: ...

    async def get_flow_run(self, run_id: str) -> FlowRunRecord | None: ...

    async def list_flow_runs(self, flow_id: str | None = None) -> tuple[FlowRunRecord, ...]: ...

    async def transition_flow_run(
        self,
        run_id: str,
        *,
        expected_version: int,
        status: RunStatus,
        result: JsonValue = None,
        error: str | None = None,
    ) -> FlowRunRecord | None: ...

    async def append_run_cycle(
        self, run_id: str, event: str, payload: JsonObject
    ) -> RunCycleRecord: ...

    async def list_run_cycles(self, run_id: str) -> tuple[RunCycleRecord, ...]: ...

    async def put_provider_key(
        self,
        provider: str,
        secret: str,
        *,
        description: str | None = None,
        is_active: bool = True,
    ) -> ProviderKeyRecord: ...

    async def update_provider_key(
        self,
        provider: str,
        *,
        expected_version: int,
        secret: str | None,
        description: str | None,
        is_active: bool,
        last_used: datetime | None,
    ) -> ProviderKeyRecord | None: ...

    async def list_provider_keys(self) -> tuple[ProviderKeyRecord, ...]: ...

    async def get_provider_secret(self, provider: str) -> str | None: ...

    async def delete_provider_key(self, provider: str, *, expected_version: int) -> bool: ...

    async def create_backtest_job(self, kind: str, request: JsonObject) -> BacktestJobRecord: ...

    async def get_backtest_job(self, job_id: str) -> BacktestJobRecord | None: ...

    async def list_backtest_jobs(self) -> tuple[BacktestJobRecord, ...]: ...

    async def transition_backtest_job(
        self,
        job_id: str,
        *,
        expected_version: int,
        status: JobStatus,
        result: JsonValue = None,
        error: str | None = None,
    ) -> BacktestJobRecord | None: ...

    async def create_artifact(
        self,
        job_id: str,
        kind: str,
        path: str,
        metadata: JsonObject,
    ) -> ArtifactRecord: ...

    async def list_artifacts(self, job_id: str | None = None) -> tuple[ArtifactRecord, ...]: ...
