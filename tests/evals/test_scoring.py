from __future__ import annotations

from fin_graph.evals.scenarios import load_scenario
from fin_graph.evals.scoring import JudgeVerdict, judge_against_reference, score_scenario


def _state(category: str = "resource_exhaustion") -> dict:
    return {
        "diagnosis": {
            "category": category,
            "root_cause": "Exit code 137 from memory in payments-api-7f9dd8b6c4-x7gr9",
            "claims": [{"statement": "Pod restarted", "evidence_ids": ["e1"]}],
        },
        "evidence": [
            {
                "id": "e1",
                "tool": "k8s_list_pods",
                "args": {},
                "data": [{"name": "pod"}],
                "error": None,
            },
            {"id": "e2", "tool": "k8s_list_events", "args": {}, "error": "API failed"},
        ],
        "findings": [{"wave": 1, "domain": "kubernetes"}],
    }


def test_forbidden_category_takes_priority() -> None:
    scenario = load_scenario("eks", "001-oomkilled-crashloop")
    scenario.answer.equivalent_root_cause_categories.append("healthy")
    score = score_scenario(scenario, _state("healthy"), elapsed=1.2)
    assert score.category_match is False
    assert score.forbidden_category is True


def test_keywords_and_unavailable_usage_are_explicit() -> None:
    scenario = load_scenario("eks", "001-oomkilled-crashloop")
    score = score_scenario(scenario, _state(), elapsed=2.5)
    assert score.category_match is True
    assert score.required_keywords_found == 3
    assert score.ruling_out_keywords_found == 0
    assert score.tokens is None
    assert score.latency_seconds == 2.5


def test_invalid_citations_and_failed_evidence() -> None:
    scenario = load_scenario("eks", "001-oomkilled-crashloop")
    state = _state()
    state["diagnosis"]["claims"].append(
        {"statement": "An event occurred", "evidence_ids": ["e2", "made-up"]}
    )
    score = score_scenario(scenario, state, elapsed=1.0)
    assert score.citations_valid == 1
    assert score.citations_total == 3
    assert score.used_sources == ["eks_pods"]


def test_trajectory_reports_unavailable_steps_and_extra_calls() -> None:
    scenario = load_scenario("eks", "001-oomkilled-crashloop")
    state = _state()
    state["evidence"].append({"id": "e3", "tool": "dd_logs", "args": {}, "error": None})
    state["evidence"][-1]["data"] = {"data": [{"message": "log"}]}
    state["findings"].append({"wave": 2, "domain": "logs"})
    score = score_scenario(scenario, state, elapsed=1.0, usage={"total_tokens": 42})
    assert score.trajectory_covered == 1
    assert score.trajectory_extra == 1
    assert score.required_queries_found == 1
    assert score.required_queries_total == 2
    assert score.investigation_waves == 2
    assert score.tokens == 42


async def test_reference_judge_is_optional_and_uses_the_golden_response() -> None:
    scenario = load_scenario("eks", "001-oomkilled-crashloop")
    assert (await judge_against_reference(scenario, _state())).status == "not_run"
    received: list[str] = []

    async def judge(reference: str, observed: str) -> JudgeVerdict:
        received.extend([reference, observed])
        return JudgeVerdict(score=0.75, reason="partial causal chain")

    result = await judge_against_reference(scenario, _state(), judge=judge)
    assert result.status == "completed"
    assert result.score == 0.75
    assert "ROOT_CAUSE" in received[0]
    assert "resource_exhaustion" in received[1]


async def test_reference_judge_failure_preserves_deterministic_result() -> None:
    scenario = load_scenario("eks", "001-oomkilled-crashloop")

    async def broken(reference: str, observed: str) -> JudgeVerdict:
        raise RuntimeError("provider timeout")

    result = await judge_against_reference(scenario, _state(), judge=broken)
    assert result.status == "failed"
    assert result.score is None


def test_rds_grafana_logs_remain_unavailable_without_a_log_fixture() -> None:
    scenario = load_scenario("rds_postgres", "001-replication-lag")
    state = {
        "diagnosis": {"category": "replication_lag", "root_cause": "lag", "claims": []},
        "evidence": [
            {
                "id": "e1",
                "tool": "aws_rds_performance",
                "args": {},
                "data": {"db_load": {"values": [1]}},
                "error": None,
            }
        ],
    }
    score = score_scenario(scenario, state, elapsed=1.0)
    assert "query_grafana_logs" in score.trajectory_unavailable
    assert score.trajectory_covered == 0


def test_empty_metric_result_cannot_satisfy_source_or_citation() -> None:
    scenario = load_scenario("rds_postgres", "001-replication-lag")
    state = {
        "diagnosis": {
            "category": "replication_lag",
            "root_cause": "lag",
            "claims": [{"statement": "lag seen", "evidence_ids": ["empty"]}],
        },
        "evidence": [
            {
                "id": "empty",
                "tool": "aws_metric_data",
                "args": {"metric_name": "ReplicaLag"},
                "data": {"MetricDataResults": [{"Label": "ReplicaLag", "Values": []}]},
                "error": None,
            }
        ],
    }
    score = score_scenario(scenario, state, elapsed=1.0)
    assert score.used_sources == []
    assert score.citations_valid == 0
    state["evidence"][0].update(
        tool="aws_rds_performance",
        data={"MetricList": [{"Key": {"Metric": "db.load.avg"}, "DataPoints": []}]},
    )
    assert score_scenario(scenario, state, elapsed=1.0).used_sources == []


def test_repeated_successful_calls_count_as_trajectory_extras() -> None:
    scenario = load_scenario("eks", "001-oomkilled-crashloop")
    state = _state()
    state["evidence"].append(
        {
            "id": "e3",
            "tool": "k8s_list_pods",
            "args": {"namespace": "payments"},
            "data": [{"name": "pod"}],
            "error": None,
        }
    )
    score = score_scenario(scenario, state, elapsed=1.0)
    assert score.trajectory_covered == 1
    assert score.trajectory_extra == 1


def test_historical_network_categories_map_to_current_taxonomy() -> None:
    dns = load_scenario("rds_postgres", "019-failover-dns-stale-endpoint")
    network = load_scenario("rds_postgres", "020-cni-loss-db-timeout-storm")
    assert score_scenario(
        dns, {"diagnosis": {"category": "dns_resolution_failure"}}, elapsed=1
    ).category_match
    assert score_scenario(
        network, {"diagnosis": {"category": "network_partition"}}, elapsed=1
    ).category_match


def test_prometheus_source_is_credited_for_normal_promql() -> None:
    scenario = load_scenario("rds_postgres", "019-failover-dns-stale-endpoint")
    state = {
        "diagnosis": {"category": "dns_resolution_failure", "claims": []},
        "evidence": [
            {
                "id": "dns",
                "tool": "prom_query_range",
                "args": {"expression": "dns_error_rate_pct"},
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {"metric": {"__name__": "dns_error_rate_pct"}, "values": [[1, "6.7"]]}
                    ],
                },
                "error": None,
            }
        ],
    }
    assert "k8s_dns_metrics" in score_scenario(scenario, state, elapsed=1).used_sources
