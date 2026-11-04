"""The metadata every integration tool carries: what it is for and what it may change."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from langchain_core.tools import BaseTool


class Domain(StrEnum):
    """The financial analyst area a tool serves."""

    FUNDAMENTALS = "fundamentals"
    VALUATION = "valuation"
    TECHNICALS = "technicals"
    SENTIMENT = "sentiment"
    BUFFETT = "buffett"
    GRAHAM = "graham"
    #: The paper portfolio and its orders: no analyst owns these tools.
    PORTFOLIO = "portfolio"


#: The financial analysts, in the order reports list them.
ANALYST_DOMAINS: tuple[Domain, ...] = (
    Domain.FUNDAMENTALS,
    Domain.VALUATION,
    Domain.TECHNICALS,
    Domain.SENTIMENT,
    Domain.BUFFETT,
    Domain.GRAHAM,
)


class SideEffectLevel(StrEnum):
    """What a call can change; anything other than ``READ_ONLY`` needs human approval."""

    READ_ONLY = "read_only"
    MUTATING = "mutating"
    EXTERNAL = "external"


class EvidenceType(StrEnum):
    """The kind of evidence a tool returns."""

    PRICES = "prices"
    FINANCIALS = "financials"
    NEWS = "news"
    INSIDER = "insider"
    VALUATION_MODEL = "valuation_model"
    INDICATORS = "indicators"
    POSITIONS = "positions"


@dataclass(frozen=True)
class ToolMeta:
    """Routing and safety facts about one tool, stored flat in ``BaseTool.metadata``."""

    integration: str
    domains: frozenset[Domain]
    evidence_type: EvidenceType
    side_effect_level: SideEffectLevel = SideEffectLevel.READ_ONLY
    idempotent: bool = True

    def as_metadata(self) -> dict[str, Any]:
        """The flat, JSON-friendly form kept on the tool."""
        return {
            "integration": self.integration,
            "domains": sorted(domain.value for domain in self.domains),
            "evidence_type": self.evidence_type.value,
            "side_effect_level": self.side_effect_level.value,
            "idempotent": self.idempotent,
        }

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any] | None) -> ToolMeta | None:
        """Read the facts back; None when the tool carries none, ValueError when malformed."""
        if not metadata or "integration" not in metadata:
            return None
        return cls(
            integration=str(metadata["integration"]),
            domains=frozenset(Domain(value) for value in metadata.get("domains", ())),
            evidence_type=EvidenceType(metadata["evidence_type"]),
            side_effect_level=SideEffectLevel(metadata["side_effect_level"]),
            idempotent=bool(metadata.get("idempotent", True)),
        )


def attach_tool_meta[ToolT: BaseTool](tool: ToolT, meta: ToolMeta) -> ToolT:
    """Store ``meta`` on ``tool`` (keeping any unrelated metadata) and return the tool."""
    tool.metadata = {**(tool.metadata or {}), **meta.as_metadata()}
    return tool


def read_tool_meta(tool: BaseTool | None) -> ToolMeta | None:
    """The tool's metadata, or None for a missing tool or one that declares nothing."""
    return None if tool is None else ToolMeta.from_metadata(tool.metadata)
