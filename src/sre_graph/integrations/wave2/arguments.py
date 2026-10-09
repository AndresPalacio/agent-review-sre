"""Finite typed operation inputs; no agent supplies endpoint paths or raw shell/query programs."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sre_graph.integrations.http import MAX_WINDOW_SECONDS


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(Arguments):
    limit: int = Field(default=50, ge=1, le=100, description="Maximum rows in this page (1-100).")
    cursor: str | None = Field(
        default=None,
        max_length=512,
        pattern=r"^[\w:.,=+/-]+$",
        description="Opaque cursor from the previous response.",
    )


class Identifier(Arguments):
    identifier: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_.:@-]*$",
        description="Provider object ID or name; no path or flags.",
    )

    @model_validator(mode="after")
    def no_traversal(self) -> Self:
        if self.identifier in {".", ".."}:
            raise ValueError("invalid identifier")
        return self


class DetailPage(Identifier, Page):
    pass


class IssueSearch(Page):
    query: str = Field(
        min_length=1, max_length=2048, description="Read-only Jira JQL issue expression."
    )


class Window(Arguments):
    start: int = Field(ge=1, description="Query start, Unix seconds.")
    end: int = Field(ge=1, description="Exclusive query end, Unix seconds, within seven days.")
    limit: int = Field(default=50, ge=1, le=100, description="Maximum rows (1-100).")

    @model_validator(mode="after")
    def bounded_window(self) -> Self:
        if self.end <= self.start or self.end - self.start > MAX_WINDOW_SECONDS:
            raise ValueError("window must increase and span at most seven days")
        return self


class Logs(Window):
    query: str = Field(
        default="",
        max_length=1024,
        description="Literal text to search, not SPL/KQL/LogsQL/DataPrime or Query DSL.",
    )


class Traces(Window):
    service: str = Field(default="", max_length=128, description="Exact service.name filter.")
    trace_id: str = Field(default="", max_length=128, description="Exact trace.trace_id filter.")

    @model_validator(mode="after")
    def require_filter(self) -> Self:
        if not self.service and not self.trace_id:
            raise ValueError("service or trace_id is required")
        return self


class GitPage(Page):
    start: int | None = Field(
        default=None, ge=1, description="Optional start Unix seconds, supplied with end."
    )
    end: int | None = Field(
        default=None, ge=1, description="Optional end Unix seconds, supplied with start."
    )

    @model_validator(mode="after")
    def optional_window(self) -> Self:
        if (self.start is None) != (self.end is None):
            raise ValueError("start and end must be supplied together")
        if (
            self.start is not None
            and self.end is not None
            and (self.end <= self.start or self.end - self.start > MAX_WINDOW_SECONDS)
        ):
            raise ValueError("window must increase and span at most seven days")
        return self


class FileRead(Arguments):
    path: str = Field(
        min_length=1,
        max_length=512,
        pattern=r"^[\w./-]+$",
        description="Repository file path; no traversal.",
    )
    ref: str = Field(
        default="main",
        min_length=1,
        max_length=128,
        pattern=r"^[\w./-]+$",
        description="Branch, tag or commit reference.",
    )

    @model_validator(mode="after")
    def validate_parts(self) -> Self:
        for value in (self.path, self.ref):
            if value.startswith(("/", "-")) or any(
                part in {"", ".", ".."} for part in value.split("/")
            ):
                raise ValueError("invalid repository path or reference")
        return self


class Release(Identifier):
    revision: int | None = Field(
        default=None, ge=1, le=1000000, description="Optional Helm revision."
    )


class ReleaseHistory(Identifier):
    limit: int = Field(default=20, ge=1, le=100, description="Maximum history rows.")
