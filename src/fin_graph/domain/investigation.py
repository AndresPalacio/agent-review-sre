"""The investigation plan (hypotheses and specialist tasks) and what specialists report back."""

from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum

from pydantic import BaseModel, Field

from fin_graph.domain.alerts.model import AlertSource, NormalizedAlert
from fin_graph.domain.tools import Domain

MAX_HYPOTHESES = 4

#: Specialist domains worth asking first for each alert source, in priority order.
SOURCE_DOMAINS: dict[AlertSource, tuple[Domain, ...]] = {
    AlertSource.ALERTMANAGER: (Domain.METRICS, Domain.LOGS, Domain.CHANGES),
    AlertSource.GRAFANA: (Domain.METRICS, Domain.LOGS, Domain.TRACES, Domain.CHANGES),
    AlertSource.DATADOG: (Domain.METRICS, Domain.LOGS, Domain.TRACES, Domain.ERRORS),
    AlertSource.CLOUDWATCH: (Domain.CLOUD, Domain.METRICS, Domain.LOGS, Domain.CHANGES),
    AlertSource.SENTRY: (Domain.ERRORS, Domain.CHANGES, Domain.LOGS),
    AlertSource.PAGERDUTY: (Domain.METRICS, Domain.LOGS, Domain.CHANGES),
    AlertSource.NEWRELIC: (Domain.METRICS, Domain.ERRORS, Domain.TRACES, Domain.LOGS),
    AlertSource.GENERIC: (Domain.METRICS, Domain.LOGS, Domain.CHANGES),
}


class Hypothesis(BaseModel):
    """A candidate explanation and how to tell whether it holds."""

    id: str = Field(description='Short id the tasks and verdicts refer to, for example "H1".')
    statement: str = Field(description="The suspected cause, in one sentence.")
    confirm_if: str = Field(description="What evidence would confirm it.")
    rule_out_if: str = Field(description="What evidence would rule it out.")


class SpecialistTask(BaseModel):
    """What one domain specialist should look into."""

    domain: Domain
    objective: str = Field(description="What to check, concretely, in this domain.")
    hypothesis_ids: list[str] = Field(
        default_factory=list, description="Hypotheses this task tests."
    )


class InvestigationPlan(BaseModel):
    """The planner's output: 1 to 4 hypotheses and the tasks that test them."""

    hypotheses: list[Hypothesis]
    tasks: list[SpecialistTask]


class Verdict(StrEnum):
    """Where the evidence leaves a hypothesis."""

    SUPPORTS = "supports"
    REFUTES = "refutes"
    INCONCLUSIVE = "inconclusive"


class HypothesisVerdict(BaseModel):
    """A specialist's verdict on one hypothesis, with the evidence behind it."""

    hypothesis_id: str
    verdict: Verdict
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class SpecialistFinding(BaseModel):
    """What a specialist found, citing the evidence ids its tool calls produced."""

    domain: Domain
    summary: str = Field(description="What the evidence shows, in two or three sentences.")
    verdicts: list[HypothesisVerdict] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, description="0 = no idea, 1 = certain.")
    gaps: list[str] = Field(default_factory=list, description="What could not be checked, and why.")


def degraded_finding(domain: Domain, error: str) -> SpecialistFinding:
    """The finding a failed specialist contributes, so the investigation can carry on."""
    return SpecialistFinding(
        domain=domain,
        summary=f"The {domain.value} specialist could not finish: {error}",
        confidence=0.0,
        gaps=[f"{domain.value} evidence is missing because the specialist failed"],
    )


def constrain_plan(
    plan: InvestigationPlan, available: Collection[Domain], max_tasks: int
) -> InvestigationPlan:
    """Keep only what can run: tasks for available domains, one per domain, ``max_tasks`` total.

    Tasks for the same domain merge their objectives; references to
    hypotheses that do not exist are dropped; at most 4 hypotheses remain.
    """
    hypotheses = plan.hypotheses[:MAX_HYPOTHESES]
    known = {hypothesis.id for hypothesis in hypotheses}
    merged: dict[Domain, SpecialistTask] = {}
    for task in plan.tasks:
        if task.domain not in available:
            continue
        ids = [item for item in task.hypothesis_ids if item in known]
        previous = merged.get(task.domain)
        if previous is None:
            merged[task.domain] = task.model_copy(update={"hypothesis_ids": _unique(ids)})
        else:
            merged[task.domain] = previous.model_copy(
                update={
                    "objective": f"{previous.objective}; {task.objective}",
                    "hypothesis_ids": _unique([*previous.hypothesis_ids, *ids]),
                }
            )
    return InvestigationPlan(hypotheses=hypotheses, tasks=list(merged.values())[:max_tasks])


def fallback_plan(
    alert: NormalizedAlert, available: Collection[Domain], max_tasks: int
) -> InvestigationPlan:
    """A deterministic plan for when the planner model fails.

    Two generic hypotheses (a recent change, or a constrained resource or
    dependency) and one task per relevant available domain: Kubernetes first
    when the alert names a pod, workload or namespace, then the source's own
    priority list.
    """
    target = alert.workload or alert.service or alert.pod or "the affected service"
    hypotheses = [
        Hypothesis(
            id="H1",
            statement=f"A recent change to {target} (deploy, config or scaling) caused the alert.",
            confirm_if="A change landed shortly before the alert started.",
            rule_out_if="No change touched it in the incident window.",
        ),
        Hypothesis(
            id="H2",
            statement=f"{target} is starved of a resource or a dependency it needs.",
            confirm_if="Saturation, errors or restarts line up with the alert.",
            rule_out_if="Resources and dependencies look normal throughout the window.",
        ),
    ]
    wanted: list[Domain] = []
    if alert.pod or alert.workload or alert.namespace:
        wanted.append(Domain.KUBERNETES)
    wanted.extend(SOURCE_DOMAINS[alert.source])
    tasks = [
        SpecialistTask(
            domain=domain,
            objective=f"Check {domain.value} evidence for {target} around the alert: {alert.title}",
            hypothesis_ids=["H1", "H2"],
        )
        for domain in _unique(wanted)
        if domain in available
    ]
    return InvestigationPlan(hypotheses=hypotheses, tasks=tasks[:max_tasks])


def _unique[T](items: list[T]) -> list[T]:
    return list(dict.fromkeys(items))
