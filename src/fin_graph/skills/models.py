"""Validated public models for inert, progressively disclosed skills."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SkillRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    category: str = Field(min_length=1)
    root: str = Field(min_length=1)
    resources: tuple[str, ...] = ()


class SkillResource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    skill_id: str
    path: str
    mime_type: str
    content: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
