"""Immutable public records persisted by compatibility stores."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from fin_graph.compat.contracts import JsonObject, JsonValue

NonBlankString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StoredRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class FlowRecord(StoredRecord):
    name: str = Field(min_length=1)
    description: str = ""
    active_revision: int = Field(ge=1)


class FlowRevision(StoredRecord):
    flow_id: str
    revision: int = Field(ge=1)
    definition: JsonObject


class FlowRunRecord(StoredRecord):
    flow_id: str
    flow_revision: int = Field(ge=1)
    status: RunStatus
    request: JsonObject
    result: JsonValue = None
    error: str | None = None


class RunCycleRecord(StoredRecord):
    run_id: str
    sequence: int = Field(ge=1)
    event: NonBlankString
    payload: JsonObject


class ProviderKeyRecord(StoredRecord):
    provider: str = Field(min_length=1)
    key_hint: str


class BacktestJobRecord(StoredRecord):
    kind: NonBlankString
    status: JobStatus
    request: JsonObject
    result: JsonValue = None
    error: str | None = None


class ArtifactRecord(StoredRecord):
    job_id: str
    kind: NonBlankString
    path: NonBlankString
    metadata: JsonObject
