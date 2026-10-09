"""Proposed remediation actions, ordered so the safest, smallest fix comes first."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from sre_graph.domain.remediation_templates import TEMPLATES
from sre_graph.domain.taxonomy import UNKNOWN
from sre_graph.domain.tools import SideEffectLevel


class Risk(StrEnum):
    """How likely the action is to make things worse."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BlastRadius(StrEnum):
    """How much the action touches, smallest first."""

    POD = "pod"
    WORKLOAD = "workload"
    SERVICE = "service"
    CLUSTER = "cluster"
    ACCOUNT = "account"


class RemediationAction(BaseModel):
    """One proposed fix. ``tool`` is the integration tool that performs it; None = manual."""

    title: str
    rationale: str = ""
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    side_effect_level: SideEffectLevel = SideEffectLevel.MUTATING
    risk: Risk = Risk.MEDIUM
    reversible: bool = True
    rollback: str = ""
    blast_radius: BlastRadius = BlastRadius.SERVICE


_RISK_ORDER = {Risk.LOW: 0, Risk.MEDIUM: 1, Risk.HIGH: 2}
_RADIUS_ORDER = {radius: rank for rank, radius in enumerate(BlastRadius)}


def order_actions(actions: Iterable[RemediationAction]) -> list[RemediationAction]:
    """Smallest blast radius first, then reversible before not, then lower risk; stable."""
    return sorted(
        actions,
        key=lambda action: (
            _RADIUS_ORDER[action.blast_radius],
            not action.reversible,
            _RISK_ORDER[action.risk],
        ),
    )


def template_steps(category: str, integrations: Collection[str]) -> list[str]:
    """Default remediation steps for ``category``, skipping steps for absent integrations.

    Categories without a template use the ``unknown`` one, so a new category
    never leaves the report without next steps.
    """
    entries = TEMPLATES.get(category, TEMPLATES[UNKNOWN])
    return [step for step, needs in entries if needs is None or needs in integrations]
