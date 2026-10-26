"""The diagnosis in incident-command form: triage, status, hypotheses, verdict and cited claims."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, create_model

from fin_graph.domain.alerts.model import NormalizedAlert
from fin_graph.domain.investigation import Verdict
from fin_graph.domain.taxonomy import CATEGORY_NAMES, UNKNOWN, render_taxonomy


class IncidentStatus(BaseModel):
    """The incident-command status line."""

    confirmed: str = Field(description="What is known for certain.")
    open: str = Field(description="What is still unknown.")
    next: str = Field(description="The next step.")
    owner: str = Field(description="Who should act: a team, a role or a person.")


class HypothesisOutcome(BaseModel):
    """How one hypothesis ended up."""

    hypothesis_id: str
    statement: str
    verdict: Verdict
    reason: str


class Claim(BaseModel):
    """A factual statement in the diagnosis and the evidence ids that back it."""

    statement: str
    evidence_ids: list[str] = Field(
        default_factory=list, description="Ids of the evidence items that support it."
    )


class Diagnosis(BaseModel):
    """The synthesizer's conclusion. ``category`` must name a taxonomy category."""

    triage_summary: str = Field(description="One line: what is affected and how badly.")
    status: IncidentStatus
    hypotheses: list[HypothesisOutcome] = Field(default_factory=list)
    verification: list[str] = Field(
        default_factory=list, description="Which checks tested which hypothesis, and the result."
    )
    follow_up_questions: list[str] = Field(
        default_factory=list, description="Direct questions for the responders."
    )
    remediation_tradeoffs: str = Field(
        default="", description='One line per fix path, or "N/A — single clear fix path".'
    )
    root_cause: str = Field(description="The root cause in two or three sentences.")
    category: str
    causal_chain: list[str] = Field(
        default_factory=list, description="The steps from cause to symptom, in order."
    )
    claims: list[Claim] = Field(default_factory=list)
    remediation_steps: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


def diagnosis_schema(categories: Collection[str] | None = None) -> type[Diagnosis]:
    """``Diagnosis`` with ``category`` as a closed enum, for structured model output.

    The JSON schema lists the allowed names (plus ``unknown``) and the field
    description carries the rendered taxonomy, so the model sees both.
    """
    allowed = (CATEGORY_NAMES if categories is None else frozenset(categories) & CATEGORY_NAMES) | {
        UNKNOWN
    }
    names = tuple(sorted(allowed))
    category_type = cast(Any, Literal.__getitem__(names))
    return create_model(
        "Diagnosis",
        __base__=Diagnosis,
        category=(
            category_type,
            Field(
                description="Exactly one category from this taxonomy:\n" + render_taxonomy(names)
            ),
        ),
    )


def unknown_diagnosis(alert: NormalizedAlert, reason: str) -> Diagnosis:
    """The diagnosis to report when the investigation could not reach a conclusion."""
    return Diagnosis(
        triage_summary=f"{alert.title}: the investigation could not reach a conclusion.",
        status=IncidentStatus(
            confirmed="The alert fired.",
            open="The root cause.",
            next="Investigate manually.",
            owner="On-call responder",
        ),
        root_cause=f"Unable to determine the root cause: {reason}",
        category=UNKNOWN,
        confidence=0.0,
    )
