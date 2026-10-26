"""Evidence: what a tool call returned, under an id that claims can cite."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fin_graph.domain.tools import Domain, EvidenceType


class EvidenceItem(BaseModel):
    """One tool result kept for the diagnosis and the report.

    ``data`` holds the tool's artifact (already capped and redacted by the
    integration layer); ``summary`` is the line a report shows for it.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    integration: str
    tool: str
    evidence_type: EvidenceType
    domains: list[Domain] = Field(default_factory=list)
    summary: str
    args: dict[str, Any] = Field(default_factory=dict)
    data: Any = None
    collected_at: datetime
    error: str | None = None


def evidence_id(tool: str, args: Mapping[str, Any]) -> str:
    """A stable id for one call: the same tool with the same arguments gets the same id.

    Parallel specialists can mint ids without coordinating, and a repeated
    call deduplicates instead of piling up.
    """
    canonical = json.dumps(
        {"tool": tool, "args": args}, sort_keys=True, default=str, separators=(",", ":")
    )
    return "ev_" + hashlib.sha256(canonical.encode()).hexdigest()[:10]


def merge_evidence(
    current: Iterable[Mapping[str, Any]] | None, new: Iterable[Mapping[str, Any]] | None
) -> list[dict[str, Any]]:
    """Graph-state reducer: append evidence dicts, one per id, keeping first-seen order.

    A later item with an id already present replaces the earlier one (the
    same call, run again, returns the fresher result).
    """
    merged: dict[str, dict[str, Any]] = {}
    for item in [*(current or []), *(new or [])]:
        merged[str(item["id"])] = dict(item)
    return list(merged.values())
