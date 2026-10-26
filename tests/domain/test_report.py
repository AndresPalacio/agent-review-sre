from __future__ import annotations

from fin_graph.domain.alerts.model import NormalizedAlert
from fin_graph.domain.claims import ClaimStatus
from fin_graph.domain.diagnosis import Claim, Diagnosis
from fin_graph.domain.evidence import EvidenceItem
from fin_graph.domain.remediation import BlastRadius, RemediationAction
from fin_graph.domain.report import build_report
from fin_graph.domain.verification import verify_diagnosis


def test_cited_evidence_is_labelled_in_order_of_first_citation(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={
            "claims": [
                Claim(statement="logs", evidence_ids=["ev_logs"]),
                Claim(statement="pods and logs", evidence_ids=["ev_pods", "ev_logs"]),
            ]
        }
    )

    report = build_report(crashloop_alert, diagnosis, pod_evidence)

    assert report.labels == {"ev_logs": "E1", "ev_pods": "E2"}
    assert [item.id for item in report.evidence] == ["ev_logs", "ev_pods"]
    assert report.claims[1].citations == ["E2", "E1"]


def test_uncited_and_missing_evidence_is_left_out(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={"claims": [Claim(statement="ghost", evidence_ids=["ev_missing", "ev_pods"])]}
    )

    report = build_report(crashloop_alert, diagnosis, pod_evidence)

    assert report.labels == {"ev_pods": "E1"}
    assert report.claims[0].citations == ["E1"]


def test_verification_results_are_attached_to_each_claim(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={"claims": [*oom_diagnosis.claims, Claim(statement="A deploy broke it.")]}
    )
    verification = verify_diagnosis(diagnosis, pod_evidence, crashloop_alert)

    report = build_report(crashloop_alert, diagnosis, pod_evidence, verification=verification)

    assert report.claims[-1].status is ClaimStatus.UNSUPPORTED
    assert report.claims[-1].reason == "cites no evidence"


def test_actions_are_ordered_and_the_title_names_the_workload(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    actions = [
        RemediationAction(title="scale up nodes", blast_radius=BlastRadius.CLUSTER),
        RemediationAction(title="raise memory limit", blast_radius=BlastRadius.WORKLOAD),
    ]

    report = build_report(crashloop_alert, oom_diagnosis, pod_evidence, actions=actions)

    assert [action.title for action in report.actions] == ["raise memory limit", "scale up nodes"]
    assert report.title == "KubePodCrashLooping — checkout"
