from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from sre_graph.evals.scenarios import load_scenario, load_suite


def test_all_36_historical_scenarios_are_available() -> None:
    assert len(load_suite("eks")) == 14
    assert len(load_suite("rds")) == 22
    assert len(load_suite("all")) == 36
    assert len(load_suite("smoke")) == 3


def test_eks_001_inherits_metadata_and_loads_its_own_evidence() -> None:
    scenario = load_scenario("eks", "001-oomkilled-crashloop")
    assert scenario.metadata["cluster_name"] == "payments-prod-eks"
    assert scenario.metadata["failure_mode"] == "oom_killed"
    assert scenario.answer.root_cause_category == "resource_exhaustion"
    assert "eks_pods" in scenario.evidence
    assert scenario.evidence["eks_pods"]["pods"][0]["containers"][0]["restart_count"] == 8
    assert scenario.evidence["eks_deployments"]["deployments"][0]["name"] == "payments-api"


def test_split_cloudwatch_metrics_are_assembled_in_stable_order() -> None:
    scenario = load_scenario("rds_postgres", "000-healthy")
    metrics = scenario.evidence["aws_cloudwatch_metrics"]["metric_data_results"]
    assert len(metrics) >= 20
    assert [item["metric_name"] for item in metrics] == sorted(
        item["metric_name"] for item in metrics
    )


def test_scenario_cannot_escape_data_root() -> None:
    with pytest.raises(ValueError):
        load_scenario("eks", "../rds_postgres/000-healthy")
    with pytest.raises(ValueError):
        load_suite("unknown")


def test_scenario_clock_uses_recorded_query_end_without_reading_the_answer() -> None:
    scenario = load_scenario("rds_postgres", "001-replication-lag")
    assert scenario.reference_time == datetime(2026, 3, 26, 11, 35, tzinfo=UTC)


def test_scenario_clock_falls_back_to_timestamped_observations() -> None:
    scenario = replace(
        load_scenario("eks", "000-healthy"),
        evidence={
            "logs": {"logs": [{"timestamp": "2026-04-14T10:30:00Z", "message": "ok"}]},
            "unrelated": {"message": "2026-12-31T00:00:00Z"},
        },
    )
    assert scenario.reference_time == datetime(2026, 4, 14, 10, 30, 1, tzinfo=UTC)


def test_undated_scenario_fails_instead_of_using_current_time() -> None:
    scenario = replace(load_scenario("eks", "000-healthy"), evidence={})
    with pytest.raises(ValueError, match="recorded time"):
        _ = scenario.reference_time
