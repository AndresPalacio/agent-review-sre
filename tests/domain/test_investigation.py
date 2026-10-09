from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from sre_graph.domain.alerts.model import AlertSource, NormalizedAlert
from sre_graph.domain.investigation import (
    Hypothesis,
    InvestigationPlan,
    SpecialistFinding,
    SpecialistTask,
    constrain_plan,
    degraded_finding,
    fallback_plan,
)
from sre_graph.domain.tools import Domain

ALL_DOMAINS = set(Domain)


def _hypothesis(identifier: str) -> Hypothesis:
    return Hypothesis(
        id=identifier, statement=f"{identifier} holds", confirm_if="seen", rule_out_if="not seen"
    )


def _alert(source: AlertSource = AlertSource.ALERTMANAGER, **fields: Any) -> NormalizedAlert:
    return NormalizedAlert(source=source, title="KubePodCrashLooping", fingerprint="f", **fields)


def test_tasks_for_unavailable_domains_are_dropped() -> None:
    plan = InvestigationPlan(
        hypotheses=[_hypothesis("H1")],
        tasks=[
            SpecialistTask(domain=Domain.KUBERNETES, objective="pods", hypothesis_ids=["H1"]),
            SpecialistTask(domain=Domain.TRACES, objective="traces", hypothesis_ids=["H1"]),
        ],
    )

    constrained = constrain_plan(plan, {Domain.KUBERNETES}, max_tasks=5)

    assert [task.domain for task in constrained.tasks] == [Domain.KUBERNETES]


def test_tasks_for_one_domain_merge_and_unknown_hypotheses_are_dropped() -> None:
    plan = InvestigationPlan(
        hypotheses=[_hypothesis("H1"), _hypothesis("H2")],
        tasks=[
            SpecialistTask(domain=Domain.LOGS, objective="errors", hypothesis_ids=["H1", "H9"]),
            SpecialistTask(domain=Domain.LOGS, objective="restarts", hypothesis_ids=["H2", "H1"]),
        ],
    )

    [task] = constrain_plan(plan, ALL_DOMAINS, max_tasks=5).tasks

    assert task.objective == "errors; restarts"
    assert task.hypothesis_ids == ["H1", "H2"]


def test_the_plan_is_capped_at_max_tasks_and_four_hypotheses() -> None:
    plan = InvestigationPlan(
        hypotheses=[_hypothesis(f"H{n}") for n in range(1, 7)],
        tasks=[SpecialistTask(domain=domain, objective="look") for domain in Domain],
    )

    constrained = constrain_plan(plan, ALL_DOMAINS, max_tasks=3)

    assert len(constrained.tasks) == 3
    assert [item.id for item in constrained.hypotheses] == ["H1", "H2", "H3", "H4"]


def test_the_fallback_plan_puts_kubernetes_first_for_a_pod_alert() -> None:
    alert = _alert(pod="checkout-7d9f8b6c5-x2x9z", workload="checkout")

    plan = fallback_plan(alert, ALL_DOMAINS, max_tasks=5)

    assert [task.domain for task in plan.tasks] == [
        Domain.KUBERNETES,
        Domain.METRICS,
        Domain.LOGS,
        Domain.CHANGES,
    ]
    assert [item.id for item in plan.hypotheses] == ["H1", "H2"]
    assert "checkout" in plan.hypotheses[0].statement


def test_the_fallback_plan_follows_the_source_and_what_is_available() -> None:
    plan = fallback_plan(_alert(AlertSource.SENTRY), {Domain.ERRORS, Domain.LOGS}, max_tasks=5)

    assert [task.domain for task in plan.tasks] == [Domain.ERRORS, Domain.LOGS]


def test_the_fallback_plan_with_nothing_available_has_no_tasks() -> None:
    assert fallback_plan(_alert(), set(), max_tasks=5).tasks == []


def test_a_degraded_finding_explains_the_gap_with_zero_confidence() -> None:
    finding = degraded_finding(Domain.LOGS, "Loki timed out")

    assert finding.confidence == 0.0
    assert "Loki timed out" in finding.summary
    assert finding.gaps


def test_finding_confidence_must_be_between_zero_and_one() -> None:
    with pytest.raises(ValidationError):
        SpecialistFinding(domain=Domain.LOGS, summary="x", confidence=1.5)
