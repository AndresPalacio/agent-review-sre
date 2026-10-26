from __future__ import annotations

from fin_graph.domain.alerts.model import AlertStatus, NormalizedAlert, Severity
from fin_graph.domain.claims import ClaimStatus
from fin_graph.domain.diagnosis import Claim, Diagnosis
from fin_graph.domain.evidence import EvidenceItem
from fin_graph.domain.verification import (
    check_claim,
    is_clearly_healthy,
    missing_sections,
    verify_diagnosis,
)


def _by_id(evidence: list[EvidenceItem]) -> dict[str, EvidenceItem]:
    return {item.id: item for item in evidence}


def test_a_well_cited_complete_diagnosis_passes(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    report = verify_diagnosis(oom_diagnosis, pod_evidence, crashloop_alert)

    assert [check.status for check in report.claims] == [ClaimStatus.SUPPORTED] * 3
    assert report.missing_sections == []
    assert report.score == 0.9
    assert report.passed is True


def test_a_claim_without_citations_is_unsupported(pod_evidence: list[EvidenceItem]) -> None:
    check = check_claim(Claim(statement="The pod is OOMKilled."), _by_id(pod_evidence))

    assert (check.status, check.reason) == (ClaimStatus.UNSUPPORTED, "cites no evidence")


def test_a_claim_citing_evidence_never_collected_is_unsupported(
    pod_evidence: list[EvidenceItem],
) -> None:
    claim = Claim(statement="A deploy happened at 09:55.", evidence_ids=["ev_made_up"])

    check = check_claim(claim, _by_id(pod_evidence))

    assert check.status is ClaimStatus.UNSUPPORTED
    assert "ev_made_up" in check.reason


def test_a_claim_with_real_and_invented_citations_is_unsupported(
    pod_evidence: list[EvidenceItem],
) -> None:
    claim = Claim(statement="The pod restarted.", evidence_ids=["ev_pods", "ev_invented"])

    check = check_claim(claim, _by_id(pod_evidence))

    assert check.status is ClaimStatus.UNSUPPORTED
    assert "ev_invented" in check.reason


def test_a_failed_tool_call_cannot_support_a_claim(pod_evidence: list[EvidenceItem]) -> None:
    failed = pod_evidence[0].model_copy(update={"error": "Forbidden", "data": None})
    claim = Claim(statement="The pod restarted.", evidence_ids=[failed.id])

    check = check_claim(claim, {failed.id: failed})

    assert check.status is ClaimStatus.UNSUPPORTED
    assert "failed" in check.reason


def test_mixed_success_and_failed_evidence_does_not_support_the_failed_part(
    pod_evidence: list[EvidenceItem],
) -> None:
    failed = pod_evidence[0].model_copy(update={"error": "Forbidden", "data": None})
    successful = pod_evidence[1]
    claim = Claim(statement="The pod restarted.", evidence_ids=[failed.id, successful.id])

    check = check_claim(claim, {failed.id: failed, successful.id: successful})

    assert check.status is ClaimStatus.UNSUPPORTED
    assert "failed" in check.reason


def test_a_claim_about_logs_that_cites_only_pod_state_is_weak(
    pod_evidence: list[EvidenceItem],
) -> None:
    claim = Claim(statement="The logs show a stack trace.", evidence_ids=["ev_pods"])

    check = check_claim(claim, _by_id(pod_evidence))

    assert check.status is ClaimStatus.WEAK
    assert "logs" in check.reason


def test_unsupported_claims_lower_the_score_and_fail_verification(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={"claims": [*oom_diagnosis.claims, Claim(statement="A deploy broke it.")]}
    )

    report = verify_diagnosis(diagnosis, pod_evidence, crashloop_alert)

    assert report.score == round(0.9 * 3 / 4, 2)
    assert report.passed is False


def test_weak_claim_cannot_pass_at_the_score_threshold_without_judgment(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={
            "category": "cpu_saturation",
            "root_cause": "CPU saturation",
            "confidence": 1.0,
            "claims": [Claim(statement="CPU saturation", evidence_ids=["ev_logs"])],
        }
    )
    log = pod_evidence[2].model_copy(update={"summary": "Application started"})

    report = verify_diagnosis(diagnosis, [log], crashloop_alert)

    assert report.claims[0].status is ClaimStatus.WEAK
    assert report.score == 0.5
    assert report.passed is False


def test_empty_incident_command_sections_are_listed(oom_diagnosis: Diagnosis) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={"follow_up_questions": [], "remediation_tradeoffs": " ", "causal_chain": []}
    )

    assert missing_sections(diagnosis) == [
        "follow_up_questions",
        "remediation_tradeoffs",
        "causal_chain",
    ]


def test_a_healthy_or_unknown_verdict_needs_no_causal_chain_or_steps(
    oom_diagnosis: Diagnosis,
) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={"category": "healthy", "causal_chain": [], "remediation_steps": []}
    )

    assert missing_sections(diagnosis) == []


def test_a_category_that_contradicts_the_root_cause_text_is_penalized(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={"root_cause": "Postgres max_connections was exhausted by a connection pool."}
    )

    report = verify_diagnosis(diagnosis, pod_evidence, crashloop_alert)

    assert report.category_mismatch is not None
    assert report.score == 0.75


def test_a_healthy_verdict_on_a_firing_critical_alert_is_a_conflict(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    diagnosis = oom_diagnosis.model_copy(update={"category": "healthy"})

    report = verify_diagnosis(diagnosis, pod_evidence, crashloop_alert)

    assert report.healthy_conflict == "a healthy verdict contradicts a firing critical alert"
    assert report.passed is False


def test_a_conclusion_without_claims_scores_zero(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    report = verify_diagnosis(
        oom_diagnosis.model_copy(update={"claims": []}), pod_evidence, crashloop_alert
    )

    assert report.score == 0.0


def test_a_resolved_informational_alert_with_evidence_is_clearly_healthy(
    crashloop_alert: NormalizedAlert,
) -> None:
    healthy = crashloop_alert.model_copy(
        update={"status": AlertStatus.RESOLVED, "severity": Severity.INFO}
    )

    assert is_clearly_healthy(healthy, evidence_count=2) is True
    assert is_clearly_healthy(healthy, evidence_count=0) is False


def test_an_alert_with_an_error_or_a_high_severity_is_never_clearly_healthy(
    crashloop_alert: NormalizedAlert,
) -> None:
    resolved = crashloop_alert.model_copy(update={"status": AlertStatus.RESOLVED})
    with_error = resolved.model_copy(
        update={"severity": Severity.INFO, "annotations": {"log_excerpt": "Traceback ..."}}
    )

    assert is_clearly_healthy(resolved, evidence_count=2) is False
    assert is_clearly_healthy(with_error, evidence_count=2) is False


def test_signal_words_match_whole_words_only(pod_evidence: list[EvidenceItem]) -> None:
    claim = Claim(
        statement="The login catalog prevented checkout from serving requests.",
        evidence_ids=["ev_pods"],
    )

    assert check_claim(claim, _by_id(pod_evidence)).status is ClaimStatus.SUPPORTED
