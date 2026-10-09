"""Deterministic checks on a diagnosis before anyone acts on it."""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel

from sre_graph.domain.alerts.model import AlertStatus, NormalizedAlert, Severity
from sre_graph.domain.diagnosis import Claim, Diagnosis
from sre_graph.domain.evidence import EvidenceItem
from sre_graph.domain.taxonomy import HEALTHY, UNKNOWN, category_text_mismatch
from sre_graph.domain.tools import EvidenceType

#: Score a diagnosis needs, with every section filled and no unsupported claim, to pass.
PASS_SCORE = 0.5
MISMATCH_PENALTY = 0.15
#: Annotations whose presence means the alert itself reports an error.
_ERROR_ANNOTATIONS = ("error", "error_message", "log_excerpt", "failed_steps")
#: Words that tie a claim to a kind of evidence; a claim using them should cite that kind.
_SIGNALS: dict[EvidenceType, re.Pattern[str]] = {
    EvidenceType.LOGS: re.compile(r"\b(?:logs?|logged|logging|stack traces?|tracebacks?)\b"),
    EvidenceType.METRICS: re.compile(
        r"\b(?:cpu|memory|latency|throughput|utili[sz]ation|saturat\w*|p9[59])\b"
    ),
    EvidenceType.TRACES: re.compile(r"\b(?:traces?|tracing|spans?)\b"),
    EvidenceType.EVENTS: re.compile(r"\b(?:events?|oomkill\w*|backoff|evicted|restart\w*)\b"),
    EvidenceType.TOPOLOGY: re.compile(r"\b(?:pods?|containers?|replicas?|nodes?|deployments?)\b"),
    EvidenceType.CHANGES: re.compile(
        r"\b(?:deploy\w*|rollouts?|releases?|commits?|revisions?|rollbacks?)\b"
    ),
    EvidenceType.ERRORS: re.compile(r"\b(?:exceptions?|error rates?|sentry issues?)\b"),
}


class ClaimStatus(StrEnum):
    """How well the cited evidence backs a claim."""

    SUPPORTED = "supported"
    WEAK = "weak"
    UNSUPPORTED = "unsupported"


class ClaimCheck(BaseModel):
    """The verdict on one claim and why."""

    statement: str
    status: ClaimStatus
    reason: str


class VerificationReport(BaseModel):
    """What the deterministic checks found and the resulting score."""

    claims: list[ClaimCheck]
    missing_sections: list[str]
    category_mismatch: str | None = None
    healthy_conflict: str | None = None
    score: float
    passed: bool

    def with_claims(self, claims: list[ClaimCheck], diagnosis: Diagnosis) -> VerificationReport:
        """Reassess the same structural checks after semantic claim judgments."""
        supported = sum(check.status is ClaimStatus.SUPPORTED for check in claims)
        weak = sum(check.status is ClaimStatus.WEAK for check in claims)
        if claims:
            backing = (supported + 0.5 * weak) / len(claims)
        else:
            backing = 1.0 if diagnosis.category in (HEALTHY, UNKNOWN) else 0.0
        score = diagnosis.confidence * backing - (
            MISMATCH_PENALTY if self.category_mismatch else 0.0
        )
        score = round(min(max(score, 0.0), 1.0), 2)
        passed = (
            not self.missing_sections
            and not self.healthy_conflict
            and all(check.status is ClaimStatus.SUPPORTED for check in claims)
            and score >= PASS_SCORE
        )
        return self.model_copy(update={"claims": claims, "score": score, "passed": passed})


def check_claim(claim: Claim, evidence: dict[str, EvidenceItem]) -> ClaimCheck:
    """Whether ``claim`` cites evidence that exists and is of the kind it talks about."""
    if not claim.evidence_ids:
        return ClaimCheck(
            statement=claim.statement, status=ClaimStatus.UNSUPPORTED, reason="cites no evidence"
        )
    missing_ids = [item for item in claim.evidence_ids if item not in evidence]
    if missing_ids:
        missing = ", ".join(missing_ids)
        return ClaimCheck(
            statement=claim.statement,
            status=ClaimStatus.UNSUPPORTED,
            reason=f"cites evidence that was never collected: {missing}",
        )
    cited = [evidence[item] for item in claim.evidence_ids]
    failed_ids = [item.id for item in cited if item.error]
    if failed_ids:
        return ClaimCheck(
            statement=claim.statement,
            status=ClaimStatus.UNSUPPORTED,
            reason=f"cites failed tool calls: {', '.join(failed_ids)}",
        )
    text = claim.statement.lower()
    mentioned = {kind for kind, pattern in _SIGNALS.items() if pattern.search(text)}
    cited_kinds = {item.evidence_type for item in cited}
    if mentioned and not mentioned & cited_kinds:
        wanted = ", ".join(sorted(kind.value for kind in mentioned))
        return ClaimCheck(
            statement=claim.statement,
            status=ClaimStatus.WEAK,
            reason=f"talks about {wanted} but cites none of that kind",
        )
    return ClaimCheck(statement=claim.statement, status=ClaimStatus.SUPPORTED, reason="ok")


def missing_sections(diagnosis: Diagnosis) -> list[str]:
    """Incident-command sections the diagnosis left empty."""
    concluded = diagnosis.category not in (HEALTHY, UNKNOWN)
    status = diagnosis.status
    required = {
        "triage_summary": bool(diagnosis.triage_summary.strip()),
        "status": all(
            part.strip() for part in (status.confirmed, status.open, status.next, status.owner)
        ),
        "hypotheses": bool(diagnosis.hypotheses),
        "verification": bool(diagnosis.verification),
        "follow_up_questions": bool(diagnosis.follow_up_questions),
        "remediation_tradeoffs": bool(diagnosis.remediation_tradeoffs.strip()),
        "root_cause": bool(diagnosis.root_cause.strip()),
        "causal_chain": bool(diagnosis.causal_chain) or not concluded,
        "remediation_steps": bool(diagnosis.remediation_steps) or not concluded,
    }
    return [name for name, present in required.items() if not present]


def is_clearly_healthy(alert: NormalizedAlert, evidence_count: int) -> bool:
    """True only when the alert is resolved, informational, error-free, and evidence exists.

    A resolved alert that was critical, or one carrying an error, is never
    "clearly healthy": a false healthy verdict hides a real incident.
    """
    return (
        alert.status is AlertStatus.RESOLVED
        and alert.severity is Severity.INFO
        and not alert.error
        and not any(alert.annotations.get(key) for key in _ERROR_ANNOTATIONS)
        and evidence_count > 0
    )


def verify_diagnosis(
    diagnosis: Diagnosis, evidence: Iterable[EvidenceItem], alert: NormalizedAlert
) -> VerificationReport:
    """Check citations, completeness, category/text agreement and healthy verdicts.

    The score is the diagnosis' confidence scaled by how well its claims are
    backed (a weak claim counts half), minus a penalty when the category does
    not match the root-cause text. With no claims, only ``healthy`` and
    ``unknown`` keep their confidence.
    """
    by_id = {item.id: item for item in evidence}
    checks = [check_claim(claim, by_id) for claim in diagnosis.claims]
    mismatch = category_text_mismatch(diagnosis.root_cause, diagnosis.category)
    conflict = None
    if (
        diagnosis.category == HEALTHY
        and alert.status is AlertStatus.FIRING
        and alert.severity in (Severity.CRITICAL, Severity.HIGH)
    ):
        conflict = f"a healthy verdict contradicts a firing {alert.severity.value} alert"
    return VerificationReport(
        claims=checks,
        missing_sections=missing_sections(diagnosis),
        category_mismatch=mismatch,
        healthy_conflict=conflict,
        score=0.0,
        passed=False,
    ).with_claims(checks, diagnosis)
