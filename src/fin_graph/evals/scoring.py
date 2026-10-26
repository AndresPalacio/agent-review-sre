"""Deterministic RCA and investigation scores for a historical scenario."""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from pydantic import BaseModel, Field

from fin_graph.evals.fixtures import SOURCE_TO_TOOL, metric_source_for
from fin_graph.evals.scenarios import Scenario

# Historical OpenSRE operation labels and the current read-tool contract.
# Steps without a recorded equivalent remain visible in trajectory_unavailable.
TRAJECTORY_TO_TOOL: dict[str, str] = {
    "list_eks_pods": "k8s_list_pods",
    "get_eks_events": "k8s_list_events",
    "get_eks_pod_logs": "k8s_pod_logs",
    "list_eks_deployments": "k8s_list_workloads",
    "get_eks_node_health": "k8s_list_nodes",
    "query_datadog_logs": "dd_logs",
    "query_datadog_monitors": "dd_monitors",
    "ec2_instances_by_tag": "aws_ec2_instances_by_tag",
    "get_elb_target_health": "aws_elb_target_health",
    "describe_rds_events": "aws_rds_events",
    "query_grafana_metrics": "aws_metric_data",
}

HISTORICAL_CATEGORY_EQUIVALENTS = {
    "failover_dns_stale_endpoint": {"dns_resolution_failure"},
    "k8s_cni_packet_loss_timeout_storm": {"network_partition"},
}


class ScenarioScore(BaseModel):
    scenario: str
    category_match: bool
    forbidden_category: bool
    required_keywords_found: int
    required_keywords_total: int
    ruling_out_keywords_found: int
    ruling_out_keywords_total: int
    forbidden_keywords_found: list[str]
    used_sources: list[str]
    required_sources_found: int
    required_sources_total: int
    citations_valid: int
    citations_total: int
    trajectory_covered: int
    trajectory_total: int
    trajectory_unavailable: list[str]
    trajectory_extra: int
    required_queries_found: int
    required_queries_total: int
    investigation_waves: int
    latency_seconds: float
    tokens: int | None
    llm_judge_status: str = "not_run"
    llm_judge_score: float | None = None
    llm_judge_reason: str = ""


class JudgeVerdict(BaseModel):
    score: float = Field(ge=0, le=1)
    reason: str


class JudgeAssessment(BaseModel):
    status: str
    score: float | None = None
    reason: str = ""


def _text(diagnosis: Mapping[str, Any]) -> str:
    parts = [
        diagnosis.get("root_cause", ""),
        diagnosis.get("triage_summary", ""),
        *(diagnosis.get("causal_chain") or []),
        *(claim.get("statement", "") for claim in diagnosis.get("claims", [])),
        *(diagnosis.get("verification") or []),
    ]
    return " ".join(str(part) for part in parts).casefold()


def _source_for(item: Mapping[str, Any], scenario: Scenario) -> str | None:
    tool = item.get("tool")
    candidates = [source for source in scenario.evidence if SOURCE_TO_TOOL.get(source) == tool]
    if len(candidates) == 1:
        return candidates[0]
    expression = str((item.get("args") or {}).get("expression", "")).casefold()
    return metric_source_for(scenario, expression)


def _has_observation(item: Mapping[str, Any]) -> bool:
    data = item.get("data")
    if data is None or data == "" or data == [] or data == {}:
        return False
    if isinstance(data, dict):
        for key in (
            "MetricDataResults",
            "MetricList",
            "data",
            "result",
            "Events",
            "samples",
            "pods",
            "instances",
            "targets",
        ):
            if key in data:
                values = data[key]
                if key == "MetricDataResults":
                    return any(result.get("Values") for result in values)
                if key == "MetricList":
                    return any(result.get("DataPoints") for result in values)
                if key == "result":
                    return any(result.get("values") for result in values)
                return bool(values)
        if "db_load" in data:
            return bool(data["db_load"].get("values"))
    return True


def score_scenario(
    scenario: Scenario,
    state: Mapping[str, Any],
    *,
    elapsed: float,
    usage: Mapping[str, int] | None = None,
) -> ScenarioScore:
    """Score recorded output; failed tool calls cannot satisfy sources or citations."""
    diagnosis = state.get("diagnosis") or {}
    answer = scenario.answer
    category = str(diagnosis.get("category", ""))
    forbidden = category in answer.forbidden_categories
    category_match = not forbidden and category in {
        answer.root_cause_category,
        *answer.equivalent_root_cause_categories,
        *HISTORICAL_CATEGORY_EQUIVALENTS.get(answer.root_cause_category, set()),
    }
    text = _text(diagnosis)
    good = [
        item
        for item in state.get("evidence", [])
        if not item.get("error") and _has_observation(item)
    ]
    valid_ids = {item["id"] for item in good}
    citations = [
        evidence_id
        for claim in diagnosis.get("claims", [])
        for evidence_id in claim.get("evidence_ids", [])
    ]
    used = sorted({source for item in good if (source := _source_for(item, scenario)) is not None})
    observed = [str(item.get("tool")) for item in good]
    expected = [TRAJECTORY_TO_TOOL.get(step) for step in answer.optimal_trajectory]
    available = {SOURCE_TO_TOOL[source] for source in scenario.evidence}
    coverable = [tool for tool in expected if tool in available]
    expected_counts = Counter(coverable)
    observed_counts = Counter(observed)
    covered = sum(min(count, observed_counts[tool]) for tool, count in expected_counts.items())
    extra = sum(max(0, count - expected_counts[tool]) for tool, count in observed_counts.items())
    waves = [int(item.get("wave", 0)) for item in state.get("findings", [])]
    return ScenarioScore(
        scenario=scenario.key,
        category_match=category_match,
        forbidden_category=forbidden,
        required_keywords_found=sum(word.casefold() in text for word in answer.required_keywords),
        required_keywords_total=len(answer.required_keywords),
        ruling_out_keywords_found=sum(
            word.casefold() in text for word in answer.ruling_out_keywords
        ),
        ruling_out_keywords_total=len(answer.ruling_out_keywords),
        forbidden_keywords_found=[
            word for word in answer.forbidden_keywords if word.casefold() in text
        ],
        used_sources=used,
        required_sources_found=sum(source in used for source in answer.required_evidence_sources),
        required_sources_total=len(answer.required_evidence_sources),
        citations_valid=sum(citation in valid_ids for citation in citations),
        citations_total=len(citations),
        trajectory_covered=covered,
        trajectory_total=len(answer.optimal_trajectory),
        trajectory_unavailable=[
            step
            for step, tool in zip(answer.optimal_trajectory, expected, strict=True)
            if tool not in available
        ],
        trajectory_extra=extra,
        required_queries_found=sum(
            TRAJECTORY_TO_TOOL.get(query) in observed for query in answer.required_queries
        ),
        required_queries_total=len(answer.required_queries),
        investigation_waves=max(waves, default=int(state.get("wave", 0))),
        latency_seconds=elapsed,
        tokens=(usage or {}).get("total_tokens"),
    )


async def judge_against_reference(
    scenario: Scenario,
    state: Mapping[str, Any],
    *,
    judge: Callable[[str, str], Awaitable[JudgeVerdict]] | None = None,
) -> JudgeAssessment:
    """Compare with the golden response only when an evaluator is configured."""
    if judge is None:
        return JudgeAssessment(status="not_run", reason="no evaluation judge was configured")
    diagnosis = state.get("diagnosis") or {}
    observed = str(
        {
            "category": diagnosis.get("category"),
            "root_cause": diagnosis.get("root_cause"),
            "causal_chain": diagnosis.get("causal_chain"),
            "verification": diagnosis.get("verification"),
            "claims": diagnosis.get("claims"),
        }
    )
    try:
        verdict = await judge(scenario.answer.model_response, observed)
    except Exception as exc:
        return JudgeAssessment(status="failed", reason=f"judge failed: {type(exc).__name__}")
    return JudgeAssessment(status="completed", score=verdict.score, reason=verdict.reason)
