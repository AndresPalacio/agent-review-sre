"""A crash-looping checkout pod: its alert, the evidence collected and the diagnosis."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sre_graph.domain.alerts.model import AlertSource, NormalizedAlert, Severity
from sre_graph.domain.diagnosis import Claim, Diagnosis, HypothesisOutcome, IncidentStatus
from sre_graph.domain.evidence import EvidenceItem
from sre_graph.domain.investigation import Verdict
from sre_graph.domain.tools import Domain, EvidenceType

COLLECTED = datetime(2026, 10, 7, 10, 6, tzinfo=UTC)


def _evidence(identifier: str, tool: str, kind: EvidenceType, summary: str) -> EvidenceItem:
    return EvidenceItem(
        id=identifier,
        integration="kubernetes",
        tool=tool,
        evidence_type=kind,
        domains=[Domain.KUBERNETES],
        summary=summary,
        collected_at=COLLECTED,
    )


@pytest.fixture
def crashloop_alert() -> NormalizedAlert:
    return NormalizedAlert(
        source=AlertSource.ALERTMANAGER,
        title="KubePodCrashLooping",
        fingerprint="f00d",
        severity=Severity.CRITICAL,
        started_at=datetime(2026, 10, 7, 10, 0, tzinfo=UTC),
        namespace="payments",
        workload="checkout",
        pod="checkout-7d9f8b6c5-x2x9z",
    )


@pytest.fixture
def pod_evidence() -> list[EvidenceItem]:
    return [
        _evidence("ev_pods", "k8s_list_pods", EvidenceType.TOPOLOGY, "1 of 3 pods restarting"),
        _evidence("ev_events", "k8s_list_events", EvidenceType.EVENTS, "BackOff after OOMKilled"),
        _evidence("ev_logs", "k8s_pod_logs", EvidenceType.LOGS, "fatal: out of memory"),
    ]


@pytest.fixture
def oom_diagnosis() -> Diagnosis:
    return Diagnosis(
        triage_summary="1 of 3 checkout replicas is crash looping; checkout still serves.",
        status=IncidentStatus(
            confirmed="checkout-7d9f8b6c5-x2x9z is OOMKilled on every start",
            open="why memory grew in release 1.4.2",
            next="raise the memory limit or roll back",
            owner="payments team",
        ),
        hypotheses=[
            HypothesisOutcome(
                hypothesis_id="H1",
                statement="The container exceeds its memory limit.",
                verdict=Verdict.SUPPORTS,
                reason="exit code 137 and OOMKilled events",
            )
        ],
        verification=["k8s_list_pods and k8s_list_events tested H1: both show OOMKilled."],
        follow_up_questions=["Did release 1.4.2 change memory settings?"],
        remediation_tradeoffs="Raising the limit is fast; rolling back also reverts features.",
        root_cause="The checkout container exceeds its 256Mi memory limit and is OOMKilled.",
        category="pod_oomkilled",
        causal_chain=["Memory grows past 256Mi", "The kernel OOM-kills PID 1", "kubelet backs off"],
        claims=[
            Claim(
                statement="The pod restarted 7 times with exit code 137.",
                evidence_ids=["ev_pods"],
            ),
            Claim(statement="Events show BackOff after OOMKilled.", evidence_ids=["ev_events"]),
            Claim(statement="The logs end with an out-of-memory error.", evidence_ids=["ev_logs"]),
        ],
        remediation_steps=["Raise the memory limit to 512Mi", "Profile memory in release 1.4.2"],
        confidence=0.9,
    )
