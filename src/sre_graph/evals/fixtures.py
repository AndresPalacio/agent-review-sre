"""Scenario-scoped fake clients that exercise the production read tools offline."""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from langchain_core.tools import BaseTool

from sre_graph.domain.tools import Domain, read_tool_meta
from sre_graph.evals.scenarios import Scenario
from sre_graph.integrations.aws.client import AwsClient
from sre_graph.integrations.aws.tools import TOOLS as AWS_TOOLS
from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.datadog.client import DatadogClient
from sre_graph.integrations.datadog.tools import TOOLS as DATADOG_TOOLS
from sre_graph.integrations.http import require_window
from sre_graph.integrations.kubernetes.client import KubernetesClient
from sre_graph.integrations.kubernetes.tools import TOOLS as KUBERNETES_TOOLS
from sre_graph.integrations.prometheus.client import PrometheusClient
from sre_graph.integrations.prometheus.tools import PROM_TOOLS

SOURCE_TO_TOOL: dict[str, str] = {
    "aws_cloudwatch_metrics": "aws_metric_data",
    "aws_performance_insights": "aws_rds_performance",
    "aws_rds_events": "aws_rds_events",
    "datadog_logs": "dd_logs",
    "datadog_monitors": "dd_monitors",
    "ec2_instances_by_tag": "aws_ec2_instances_by_tag",
    "eks_deployments": "k8s_list_workloads",
    "eks_events": "k8s_list_events",
    "eks_node_health": "k8s_list_nodes",
    "eks_pod_logs": "k8s_pod_logs",
    "eks_pods": "k8s_list_pods",
    "elb_target_health": "aws_elb_target_health",
    "k8s_dns_metrics": "prom_query_range",
    "k8s_events": "k8s_list_events",
    "k8s_mesh_metrics": "prom_query_range",
    "k8s_node_metrics": "prom_query_range",
    "k8s_pod_metrics": "prom_query_range",
    "k8s_rollout": "k8s_rollout_history",
}

_ALL_TOOLS = {
    tool.name: tool for tool in (*AWS_TOOLS, *DATADOG_TOOLS, *KUBERNETES_TOOLS, *PROM_TOOLS)
}


@dataclass(frozen=True)
class FixtureCoverage:
    available: tuple[str, ...]
    missing: tuple[str, ...]


class FixtureRegistry:
    """Only present tools whose evidence exists in this scenario."""

    def __init__(self, names: set[str]) -> None:
        self._names = names

    def tools(
        self, context: RunContext, domains: Collection[Domain] | None = None
    ) -> list[BaseTool]:
        selected = []
        for name in sorted(self._names):
            tool = _ALL_TOOLS[name]
            meta = read_tool_meta(tool)
            if meta is None or meta.integration not in context.clients:
                continue
            if domains is None or meta.domains & set(domains):
                selected.append(tool)
        return selected


def _evidence(scenario: Scenario, source: str) -> dict[str, Any]:
    try:
        return scenario.evidence[source]
    except KeyError as exc:
        raise IntegrationError(f"{source} was not recorded for {scenario.key}") from exc


def _in_window(timestamp: str, start: int, end: int) -> bool:
    seconds = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    return start <= seconds <= end


def _mentions(expression: str, name: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", expression) is not None


class FixtureKubernetes(KubernetesClient):
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario

    async def list_pods(
        self, namespace: str, label_selector: str | None = None, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        pods = _evidence(self.scenario, "eks_pods")["pods"]
        if label_selector:
            key, separator, value = label_selector.partition("=")
            if not separator or not key or not value or not all("labels" in item for item in pods):
                raise IntegrationError("pod labels or selector syntax were not recorded")
            pods = [item for item in pods if item["labels"].get(key) == value]
        return [item for item in pods[:limit] if item.get("namespace") == namespace]

    async def pod_logs(
        self,
        namespace: str,
        name: str,
        *,
        container: str | None = None,
        previous: bool = False,
        tail_lines: int = 200,
        since_seconds: int | None = None,
    ) -> str:
        data = _evidence(self.scenario, "eks_pod_logs")
        if data["namespace"] != namespace or data["pod_name"] != name:
            raise IntegrationError(f"logs for {namespace}/{name} were not recorded")
        return str(data["logs"])

    async def list_events(
        self, namespace: str, *, involved_object: str | None = None, warnings_only: bool = False
    ) -> list[dict[str, Any]]:
        source = "eks_events" if "eks_events" in self.scenario.evidence else "k8s_events"
        data = _evidence(self.scenario, source)
        events = data.get("warning_events", data.get("events", []))
        return [
            event
            for event in events
            if event.get("namespace", namespace) == namespace
            and (not warnings_only or event.get("type", "Warning") == "Warning")
            and (not involved_object or involved_object in str(event))
        ]

    async def list_workloads(
        self, namespace: str, label_selector: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            item
            for item in _evidence(self.scenario, "eks_deployments")["deployments"]
            if item.get("namespace") == namespace
        ]

    async def list_nodes(self) -> list[dict[str, Any]]:
        return list(_evidence(self.scenario, "eks_node_health")["nodes"])

    async def rollout_history(self, namespace: str, deployment: str) -> list[dict[str, Any]]:
        data = _evidence(self.scenario, "k8s_rollout")
        if data["namespace"] != namespace or data["service"] != deployment:
            raise IntegrationError(f"rollout for {namespace}/{deployment} was not recorded")
        return [data]


class FixtureAws(AwsClient):
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario

    async def metric_data(
        self,
        namespace: str,
        metric_name: str,
        start: int,
        end: int,
        dimension_name: str = "",
        dimension_value: str = "",
        next_token: str | None = None,
    ) -> dict[str, Any]:
        require_window(start, end)
        data = _evidence(self.scenario, "aws_cloudwatch_metrics")
        matches = [
            item
            for item in data["metric_data_results"]
            if item.get("metric_name", item.get("label")) == metric_name
            and namespace in data["namespace"].split(",")
            and (
                not dimension_name
                or {"Name": dimension_name, "Value": dimension_value} in item.get("dimensions", [])
            )
        ]
        if not matches:
            raise IntegrationError(f"metric {namespace}/{metric_name} was not recorded")
        return {
            "MetricDataResults": [
                {
                    "Id": item.get("id", "m1"),
                    "Label": item.get("label", metric_name),
                    "Timestamps": [
                        ts for ts in item.get("timestamps", []) if _in_window(ts, start, end)
                    ],
                    "Values": [
                        value
                        for ts, value in zip(
                            item.get("timestamps", []), item.get("values", []), strict=True
                        )
                        if _in_window(ts, start, end)
                    ],
                    "StatusCode": item.get("status_code", "Complete"),
                }
                for item in matches
            ]
        }

    async def rds_events(
        self, identifier: str, start: int, end: int, marker: str | None = None
    ) -> dict[str, Any]:
        require_window(start, end)
        events = _evidence(self.scenario, "aws_rds_events")["events"]
        return {
            "Events": [
                {
                    "Date": event.get("date"),
                    "Message": event.get("message"),
                    "SourceIdentifier": event.get("source_identifier"),
                    "SourceType": event.get("source_type", "db-instance"),
                }
                for event in events
                if event.get("source_identifier") == identifier
                and _in_window(event["date"], start, end)
            ]
        }

    async def rds_performance(
        self, resource_id: str, start: int, end: int, next_token: str | None = None
    ) -> dict[str, Any]:
        require_window(start, end)
        data = _evidence(self.scenario, "aws_performance_insights")
        if resource_id != data["db_instance_identifier"]:
            raise IntegrationError(f"performance for {resource_id} was not recorded")
        load = data["db_load"]
        points = [
            {"Timestamp": timestamp, "Value": value}
            for timestamp, value in zip(load["timestamps"], load["values"], strict=True)
            if _in_window(timestamp, start, end)
        ]
        metric_list = [{"Key": {"Metric": "db.load.avg"}, "DataPoints": points}]
        if points:
            metric_list.extend(
                {
                    "Key": {
                        "Metric": "db.load.avg",
                        "Dimensions": {"db.sql_tokenized.statement": item["statement"]},
                    },
                    "DataPoints": [
                        {"Timestamp": points[-1]["Timestamp"], "Value": item["db_load_avg"]}
                    ],
                }
                for item in data["top_sql"][:10]
            )
            metric_list.extend(
                {
                    "Key": {
                        "Metric": "db.load.avg",
                        "Dimensions": {
                            "db.wait_event.name": item["name"],
                            "db.wait_event.type": item["type"],
                        },
                    },
                    "DataPoints": [
                        {"Timestamp": points[-1]["Timestamp"], "Value": item["db_load_avg"]}
                    ],
                }
                for item in data["top_wait_events"][:10]
            )
        return {"MetricList": metric_list}

    async def ec2_instances_by_tag(self, tag_key: str, tag_value: str) -> dict[str, Any]:
        data = _evidence(self.scenario, "ec2_instances_by_tag")
        matches = [item for item in data["instances"] if item.get(tag_key) == tag_value]
        if not matches:
            raise IntegrationError(f"EC2 tag {tag_key}={tag_value} was not recorded")
        return {"instances": matches}

    async def elb_target_health(self, target_group_arn: str) -> dict[str, Any]:
        data = _evidence(self.scenario, "elb_target_health")
        targets = [
            item for item in data["targets"] if item.get("target_group_arn") == target_group_arn
        ]
        if not targets:
            raise IntegrationError(f"ELB target group {target_group_arn} was not recorded")
        return {"targets": targets}


class FixtureDatadog(DatadogClient):
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario

    async def logs(
        self, query: str, start: int, end: int, limit: int = 100, cursor: str | None = None
    ) -> dict[str, Any]:
        require_window(start, end)
        if cursor is not None or not 1 <= limit <= 100:
            raise IntegrationError("fixture log pagination was not recorded")
        filters: dict[str, str] = {}
        if query.strip() != "*":
            for token in query.split():
                match = re.fullmatch(r"(service|status|host):([A-Za-z0-9_.-]+)", token)
                if match is None:
                    raise IntegrationError("fixture log query cannot be represented")
                filters[match.group(1)] = match.group(2)
        data = _evidence(self.scenario, "datadog_logs")
        logs = [
            item
            for item in data["logs"]
            if _in_window(item["timestamp"], start, end)
            and all(str(item.get(key)) == value for key, value in filters.items())
        ]
        return {
            "data": [
                {"id": f"fixture-log-{index}", "type": "log", "attributes": item}
                for index, item in enumerate(logs[:limit], start=1)
            ]
        }

    async def monitors(self, limit: int = 100) -> Any:
        return _evidence(self.scenario, "datadog_monitors")["monitors"][:limit]


class FixturePrometheus(PrometheusClient):
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario

    async def query_range(self, expression: str, start: int, end: int, step: int) -> Any:
        require_window(start, end)
        if step <= 0:
            raise IntegrationError("query step must be positive")
        source = metric_source_for(self.scenario, expression)
        if source is None:
            raise IntegrationError("PromQL does not identify one recorded metric source")
        data = _evidence(self.scenario, source)
        metric_names = {
            name
            for sample in data["samples"]
            for name, value in sample.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        requested = {name for name in metric_names if _mentions(expression, name)}
        if not requested:
            raise IntegrationError(
                "PromQL metric was not recorded; available: " + ", ".join(sorted(metric_names))
            )
        series: dict[tuple[tuple[str, str], ...], list[list[int | str]]] = {}
        common = {str(key): str(value) for key, value in data.items() if key != "samples"}
        for sample in data["samples"]:
            if not _in_window(sample["ts"], start, end):
                continue
            labels = common | {
                key: str(value)
                for key, value in sample.items()
                if key != "ts" and not isinstance(value, (int, float))
            }
            timestamp = int(datetime.fromisoformat(sample["ts"].replace("Z", "+00:00")).timestamp())
            for name, value in sample.items():
                if (
                    name in requested
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                ):
                    key = tuple(sorted((labels | {"__name__": name}).items()))
                    series.setdefault(key, []).append([timestamp, str(value)])
        return {
            "resultType": "matrix",
            "result": [
                {"metric": dict(labels), "values": values}
                for labels, values in sorted(series.items())
            ],
        }


_METRIC_HINTS = {
    "k8s_dns_metrics": ("k8s_dns_metrics", "coredns", "dns_", "nxdomain"),
    "k8s_mesh_metrics": ("k8s_mesh_metrics", "istio", "envoy", "mesh", "tcp_connect"),
    "k8s_node_metrics": ("k8s_node_metrics", "node_", "packet_drop", "retransmit"),
    "k8s_pod_metrics": ("k8s_pod_metrics", "pod_", "kube_pod", "http_request", "retry"),
}


def metric_source_for(scenario: Scenario, expression: str) -> str | None:
    """Resolve one recorded synthetic metric source from plausible PromQL."""
    lowered = expression.lower()
    candidates = [
        source
        for source, hints in _METRIC_HINTS.items()
        if source in scenario.evidence
        and (
            any(hint in lowered for hint in hints)
            or any(
                _mentions(lowered, name)
                for sample in scenario.evidence[source]["samples"]
                for name, value in sample.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            )
        )
    ]
    return candidates[0] if len(candidates) == 1 else None


class FixtureBackend:
    """One immutable scenario's recorded evidence; no service client is constructed."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        missing = sorted(
            source
            for source in scenario.evidence
            if source not in SOURCE_TO_TOOL or SOURCE_TO_TOOL[source] not in _ALL_TOOLS
        )
        self.coverage = FixtureCoverage(tuple(sorted(scenario.evidence)), tuple(missing))
        tools = {SOURCE_TO_TOOL[source] for source in scenario.evidence if source not in missing}
        self.registry = FixtureRegistry(tools)
        metas = [read_tool_meta(_ALL_TOOLS[name]) for name in tools]
        if any(meta is None for meta in metas):
            raise ValueError("fixture tool is missing metadata")
        integrations = {meta.integration for meta in metas if meta is not None}
        clients: dict[str, object] = {}
        if "kubernetes" in integrations:
            clients["kubernetes"] = FixtureKubernetes(scenario)
        if "aws" in integrations:
            clients["aws"] = FixtureAws(scenario)
        if "datadog" in integrations:
            clients["datadog"] = FixtureDatadog(scenario)
        if "prometheus" in integrations:
            clients["prometheus"] = FixturePrometheus(scenario)
        self.context = RunContext(clients=clients)
