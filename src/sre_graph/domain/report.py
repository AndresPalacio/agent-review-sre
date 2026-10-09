"""The RCA report: everything a renderer needs, with evidence labelled E1, E2… as cited."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field

from sre_graph.domain.alerts.model import NormalizedAlert
from sre_graph.domain.diagnosis import Diagnosis
from sre_graph.domain.evidence import EvidenceItem
from sre_graph.domain.remediation import RemediationAction, order_actions
from sre_graph.domain.verification import ClaimStatus, VerificationReport


class ReportClaim(BaseModel):
    """A claim as the report shows it: its citations as labels, and any doubt about it."""

    statement: str
    citations: list[str] = Field(default_factory=list)
    status: ClaimStatus | None = None
    reason: str = ""


class Report(BaseModel):
    """One investigation's outcome, ready to render for any channel."""

    alert: NormalizedAlert
    diagnosis: Diagnosis
    verification: VerificationReport | None = None
    claims: list[ReportClaim] = Field(default_factory=list)
    actions: list[RemediationAction] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    duration_seconds: float | None = None

    @property
    def title(self) -> str:
        """The alert title, with the affected service when the title does not name it."""
        target = self.alert.workload or self.alert.service
        if target and target not in self.alert.title:
            return f"{self.alert.title} — {target}"
        return self.alert.title


def build_report(
    alert: NormalizedAlert,
    diagnosis: Diagnosis,
    evidence: Iterable[EvidenceItem],
    *,
    verification: VerificationReport | None = None,
    actions: Iterable[RemediationAction] = (),
    duration_seconds: float | None = None,
) -> Report:
    """Assemble a report: cited evidence only, labelled in order of first citation.

    Citations of evidence that does not exist are dropped from the labels
    (verification already flags those claims as unsupported).
    """
    by_id = {item.id: item for item in evidence}
    labels: dict[str, str] = {}
    for claim in diagnosis.claims:
        for item in claim.evidence_ids:
            if item in by_id and item not in labels:
                labels[item] = f"E{len(labels) + 1}"
    checks = verification.claims if verification is not None else []
    claims = [
        ReportClaim(
            statement=claim.statement,
            citations=[labels[item] for item in claim.evidence_ids if item in labels],
            status=checks[index].status if index < len(checks) else None,
            reason=checks[index].reason if index < len(checks) else "",
        )
        for index, claim in enumerate(diagnosis.claims)
    ]
    return Report(
        alert=alert,
        diagnosis=diagnosis,
        verification=verification,
        claims=claims,
        actions=order_actions(actions),
        evidence=[by_id[item] for item in labels],
        labels=labels,
        duration_seconds=duration_seconds,
    )
