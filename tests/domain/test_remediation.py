from __future__ import annotations

from fin_graph.domain.remediation import (
    BlastRadius,
    RemediationAction,
    Risk,
    order_actions,
    template_steps,
)
from fin_graph.domain.remediation_templates import TEMPLATES
from fin_graph.domain.taxonomy import CATEGORY_NAMES


def test_the_smallest_reversible_lowest_risk_action_comes_first() -> None:
    restart_cluster = RemediationAction(title="drain nodes", blast_radius=BlastRadius.CLUSTER)
    delete_pod = RemediationAction(
        title="delete pod", blast_radius=BlastRadius.POD, risk=Risk.LOW, reversible=False
    )
    undo = RemediationAction(
        title="rollout undo", blast_radius=BlastRadius.WORKLOAD, risk=Risk.HIGH
    )
    restart = RemediationAction(
        title="rollout restart", blast_radius=BlastRadius.WORKLOAD, risk=Risk.LOW
    )

    ordered = order_actions([restart_cluster, undo, delete_pod, restart])

    assert [action.title for action in ordered] == [
        "delete pod",
        "rollout restart",
        "rollout undo",
        "drain nodes",
    ]


def test_every_template_names_a_real_category() -> None:
    assert set(TEMPLATES) <= CATEGORY_NAMES
    assert "unknown" in TEMPLATES


def test_steps_for_absent_integrations_are_skipped() -> None:
    without = template_steps("connection_exhaustion", integrations=set())
    with_grafana = template_steps("connection_exhaustion", integrations={"grafana"})

    assert not any("Grafana" in step for step in without)
    assert any("Grafana" in step for step in with_grafana)
    assert not any("Datadog" in step for step in with_grafana)


def test_a_category_without_a_template_uses_the_unknown_one() -> None:
    assert template_steps("readiness_probe_misconfigured", set()) == template_steps(
        "unknown", set()
    )
