"""Transport-independent commands for the local work pilot."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fin_graph.work.policy import Decision


class WorkConflict(ValueError):
    """A command conflicts with an earlier command or work revision."""


class WorkUnavailable(RuntimeError):
    """Remote execution could not be confirmed; retry the same command ID."""


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    command_id: str = Field(min_length=1, max_length=128)


class StartWork(Command):
    text: str = Field(min_length=1, max_length=16000)
    deadline_seconds: float = Field(default=300, ge=1, le=3600)
    delay_seconds: float = Field(default=0.2, ge=0, le=30)


class UpdateWork(Command):
    expected_revision: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=16000)


class CancelWork(Command):
    expected_revision: int = Field(ge=1)


class WorkView(BaseModel):
    work_id: str
    revision: int
    status: str
    run_id: str | None = None
    decision: Decision
    result: str | None = None
    children: list[dict[str, Any]] = Field(default_factory=list)
    decision_trace: list[dict[str, Any]] = Field(default_factory=list)
    model_mode: str = "unknown"
    data_mode: str = "synthetic"
    detail: str | None = None
