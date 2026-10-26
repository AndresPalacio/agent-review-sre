from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from fin_graph.agents.factory import make_agent
from fin_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.evals.fixtures import SOURCE_TO_TOOL, FixtureBackend
from fin_graph.evals.scenarios import load_scenario, load_suite


async def _call(backend: FixtureBackend, name: str, args: dict[str, Any]) -> ToolMessage:
    model = ScriptedChatModel(responses=[tool_call_message(name, args, "c1"), AIMessage("done")])
    agent = make_agent(
        name="fixture_test",
        model=model,
        tools=backend.registry.tools(backend.context),
        context_schema=type(backend.context),
    )
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="read fixture")]}, context=backend.context
    )
    return next(item for item in result["messages"] if isinstance(item, ToolMessage))


def test_all_declared_sources_have_a_tool_mapping() -> None:
    sources = {source for scenario in load_suite("all") for source in scenario.evidence}
    assert len(sources) == 18
    assert sources <= set(SOURCE_TO_TOOL)
    assert all(not FixtureBackend(scenario).coverage.missing for scenario in load_suite("all"))


def test_unknown_source_is_reported_and_never_exposed_as_a_tool() -> None:
    scenario = load_scenario("eks", "001-oomkilled-crashloop")
    extended = replace(scenario, evidence=scenario.evidence | {"unknown_source": {"secret": "x"}})
    backend = FixtureBackend(extended)
    assert backend.coverage.missing == ("unknown_source",)
    assert "unknown_source" not in {tool.name for tool in backend.registry.tools(backend.context)}


def test_backend_exposes_only_tools_for_declared_sources() -> None:
    backend = FixtureBackend(load_scenario("eks", "001-oomkilled-crashloop"))
    names = {tool.name for tool in backend.registry.tools(backend.context)}
    assert "k8s_list_pods" in names
    assert "k8s_pod_logs" in names
    assert "aws_metric_data" not in names
    assert "k8s_list_hpas" not in names


async def test_kubernetes_tool_reads_scenario_pods_offline() -> None:
    backend = FixtureBackend(load_scenario("eks", "001-oomkilled-crashloop"))
    result = await _call(backend, "k8s_list_pods", {"namespace": "payments"})
    assert result.status == "success"
    assert result.artifact[0]["containers"][0]["restart_count"] == 8
    narrowed = await _call(
        backend,
        "k8s_list_pods",
        {"namespace": "payments", "label_selector": "app=definitely-absent"},
    )
    assert narrowed.status == "error"


async def test_aws_metric_tool_filters_fixture_and_rejects_missing_metric() -> None:
    backend = FixtureBackend(load_scenario("rds_postgres", "001-replication-lag"))
    args = {
        "namespace": "AWS/RDS",
        "metric_name": "ReplicaLag",
        "start": 1774524000,
        "end": 1774524900,
    }
    found = await _call(backend, "aws_metric_data", args)
    assert found.status == "success"
    assert found.artifact["MetricDataResults"][0]["Label"] == "ReplicaLag"
    missing = await _call(backend, "aws_metric_data", args | {"metric_name": "NotRecorded"})
    assert missing.status == "error"
    stale = await _call(backend, "aws_metric_data", args | {"start": 100, "end": 200})
    assert stale.artifact["MetricDataResults"][0]["Values"] == []


async def test_performance_and_events_match_the_aws_read_contract() -> None:
    backend = FixtureBackend(load_scenario("rds_postgres", "001-replication-lag"))
    args = {"start": 1774524000, "end": 1774524900}
    performance = await _call(
        backend,
        "aws_rds_performance",
        {"resource_id": "payments-prod", **args},
    )
    events = await _call(
        backend,
        "aws_rds_events",
        {"identifier": "payments-prod-replica-1", **args},
    )
    assert performance.status == "success"
    assert "top_sql" not in performance.artifact
    assert len(performance.artifact["MetricList"]) >= 3
    assert any(
        "db.sql_tokenized.statement" in metric["Key"].get("Dimensions", {})
        for metric in performance.artifact["MetricList"]
    )
    assert events.status == "success"
    assert "SourceIdentifier" in events.artifact["Events"][0]


async def test_ec2_and_elb_tools_read_upstream_fixture() -> None:
    backend = FixtureBackend(load_scenario("rds_upstream", "001-request-burst-ec2-app-tier"))
    ec2 = await _call(backend, "aws_ec2_instances_by_tag", {"tag_key": "tier", "tag_value": "web"})
    target_group = (
        "arn:aws:elasticloadbalancing:us-east-1:111122223333:targetgroup/orders-web-tg/def456"
    )
    elb = await _call(
        backend,
        "aws_elb_target_health",
        {"target_group_arn": target_group},
    )
    assert ec2.artifact["instances"][0]["tier"] == "web"
    assert elb.artifact["targets"][0]["instance_id"] == "i-0a11"


async def test_datadog_tools_read_only_the_current_scenario() -> None:
    backend = FixtureBackend(load_scenario("eks", "001-oomkilled-crashloop"))
    start = int(datetime.fromisoformat("2026-04-14T10:00:00+00:00").timestamp())
    end = int(datetime.fromisoformat("2026-04-14T11:00:00+00:00").timestamp())
    logs = await _call(
        backend, "dd_logs", {"query": "service:payments-api", "start": start, "end": end}
    )
    unrelated = await _call(
        backend, "dd_logs", {"query": "service:does-not-exist", "start": start, "end": end}
    )
    stale = await _call(
        backend, "dd_logs", {"query": "service:payments-api", "start": 100, "end": 200}
    )
    monitors = await _call(backend, "dd_monitors", {})
    assert logs.status == "success"
    assert logs.artifact["data"][0]["type"] == "log"
    assert logs.artifact["data"][0]["attributes"]["service"] == "payments-api"
    assert unrelated.artifact["data"] == []
    assert stale.artifact["data"] == []
    assert monitors.status == "success"
    assert monitors.artifact[0]["name"].startswith("[payments]")


async def test_prometheus_fixture_requires_a_declared_source_in_expression() -> None:
    backend = FixtureBackend(load_scenario("rds_postgres", "019-failover-dns-stale-endpoint"))
    start = int(datetime.fromisoformat("2026-04-12T06:20:00+00:00").timestamp())
    end = int(datetime.fromisoformat("2026-04-12T06:30:00+00:00").timestamp())
    args = {
        "expression": "dns_error_rate_pct",
        "start": start,
        "end": end,
        "step": 60,
    }
    found = await _call(backend, "prom_query_range", args)
    missing = await _call(backend, "prom_query_range", args | {"expression": "cpu_usage"})
    assert found.status == "success"
    assert found.artifact["resultType"] == "matrix"
    assert {series["metric"]["__name__"] for series in found.artifact["result"]} == {
        "dns_error_rate_pct"
    }
    assert any(series["metric"].get("resolver") == "coredns" for series in found.artifact["result"])
    assert missing.status == "error"
    unsupported = await _call(
        backend,
        "prom_query_range",
        args | {"expression": "rate(coredns_dns_requests_total[5m])"},
    )
    assert unsupported.status == "error"
    stale = await _call(backend, "prom_query_range", args | {"start": 100, "end": 200})
    assert stale.artifact["result"] == []
