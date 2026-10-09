from __future__ import annotations

import pytest

from sre_graph.runbooks.catalog import load_runbook, select_runbooks
from sre_graph.skills.catalog import load_skill


def test_runbook_requires_exact_trusted_id() -> None:
    assert "Kubernetes" in load_runbook("kubernetes-rollout")
    with pytest.raises(KeyError):
        load_runbook("../kubernetes-rollout")
    with pytest.raises(KeyError):
        load_runbook("kubernetes-rollout extra")


def test_runbook_selection_requires_exact_service_or_category() -> None:
    assert [item["id"] for item in select_runbooks("checkout", "deployment_rollout_stuck")] == [
        "kubernetes-rollout"
    ]
    assert [item["id"] for item in select_runbooks("postgres", None)] == ["database-saturation"]
    assert select_runbooks("postgres-prod", "deployment_rollout_stuck extra") == []


def test_skill_discloses_summary_before_full_playbook() -> None:
    summary = load_skill("investigating-incidents")
    full = load_skill("investigating-incidents", full=True)
    assert "evidencia" in summary.lower()
    assert len(full) > len(summary)
    with pytest.raises(KeyError):
        load_skill("../../secrets", full=True)
