"""Exact-ID runbook selection; documents are guidance, never executable orders."""

from __future__ import annotations

RUNBOOKS = {
    "kubernetes-rollout": (
        "# Kubernetes rollout\n"
        "Check deployment conditions, revision history, affected pods and recent changes. "
        "Compare current and previous revisions. Suggest rollback only when evidence links "
        "the rollout to the failure. Any mutation needs a separate human approval."
    ),
    "database-saturation": (
        "# Database saturation\n"
        "Check connection count, CPU, storage, latency and recent queries over the alert window. "
        "Distinguish a database bottleneck from upstream load before proposing a fix."
    ),
}


def load_runbook(runbook_id: str) -> str:
    """Return only an explicitly packaged guide by its exact identifier."""
    return RUNBOOKS[runbook_id]


def select_runbooks(service: str | None, category: str | None) -> list[dict[str, str]]:
    """Select packaged guides from exact alert identity, never fuzzy user text."""
    selected: list[str] = []
    if category == "deployment_rollout_stuck":
        selected.append("kubernetes-rollout")
    if service in {"postgres", "postgresql", "mysql", "database"} or category in {
        "connection_exhaustion",
        "cpu_saturation_bad_query",
    }:
        selected.append("database-saturation")
    return [{"id": item, "content": load_runbook(item)} for item in selected]
