"""Read-only AWS cloud, metrics, logs and change tools."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from fin_graph.domain.tools import Domain, EvidenceType, ToolMeta
from fin_graph.integrations.aws.client import AwsClient
from fin_graph.integrations.context import RunContext, require_client
from fin_graph.integrations.payload import tool_payload
from fin_graph.integrations.tooling import integration_tool


def _meta(domains: set[Domain], kind: EvidenceType) -> ToolMeta:
    return ToolMeta(integration="aws", domains=frozenset(domains), evidence_type=kind)


def _client(runtime: ToolRuntime[RunContext]) -> AwsClient:
    return require_client(runtime, "aws", AwsClient)


def _result(data: Any) -> tuple[str, Any]:
    return tool_payload(data), data


def _page_result(data: dict[str, Any]) -> tuple[str, Any]:
    """Name the SDK's top-level cursor without exempting token keys from redaction.

    Only paginated AWS reads use this boundary; nested NextToken values remain
    credential-like fields. Cursor values still pass all credential format scans.
    """
    if "NextToken" in data:
        data = {
            "pagination_cursor": data["NextToken"],
            **{key: value for key, value in data.items() if key != "NextToken"},
        }
    return _result(data)


@integration_tool(_meta({Domain.CLOUD, Domain.METRICS}, EvidenceType.EVENTS))
async def aws_alarms(
    runtime: ToolRuntime[RunContext], limit: int = 20, next_token: str | None = None
) -> tuple[str, Any]:
    """List one page of CloudWatch alarms.

    Args:
        limit: Maximum alarms, up to 100.
        next_token: Optional pagination_cursor returned by a previous page.
    """
    return _page_result(await _client(runtime).alarms(limit, next_token))


@integration_tool(_meta({Domain.METRICS, Domain.CLOUD}, EvidenceType.METRICS))
async def aws_metric_data(
    namespace: str,
    metric_name: str,
    start: int,
    end: int,
    runtime: ToolRuntime[RunContext],
    dimension_name: str = "",
    dimension_value: str = "",
    next_token: str | None = None,
) -> tuple[str, Any]:
    """Read one CloudWatch metric in a bounded window.

    Args:
        namespace: AWS metric namespace.
        metric_name: Metric name.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        dimension_name: Optional dimension name.
        dimension_value: Optional dimension value.
        next_token: Optional pagination_cursor returned by a previous page.
    """
    return _page_result(
        await _client(runtime).metric_data(
            namespace, metric_name, start, end, dimension_name, dimension_value, next_token
        )
    )


@integration_tool(_meta({Domain.LOGS, Domain.CLOUD}, EvidenceType.LOGS))
async def aws_logs_start(
    log_group: str,
    query: str,
    start: int,
    end: int,
    runtime: ToolRuntime[RunContext],
    limit: int = 50,
) -> tuple[str, Any]:
    """Start one bounded CloudWatch Logs Insights read query.

    Args:
        log_group: Exact log group name.
        query: Logs Insights query text.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Maximum result rows, up to 100.
    """
    return _result(await _client(runtime).logs_start(log_group, query, start, end, limit))


@integration_tool(_meta({Domain.LOGS, Domain.CLOUD}, EvidenceType.LOGS))
async def aws_logs_results(query_id: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """Wait within the AWS timeout for terminal Logs Insights state and rows; retry if in progress.

    Args:
        query_id: Query ID returned by aws_logs_start.
    """
    return _result(await _client(runtime).logs_results(query_id))


@integration_tool(_meta({Domain.CLOUD, Domain.KUBERNETES}, EvidenceType.TOPOLOGY))
async def aws_eks_cluster(name: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """Read EKS cluster status and configuration.

    Args:
        name: Exact cluster name.
    """
    return _result(await _client(runtime).eks_cluster(name))


@integration_tool(_meta({Domain.CLOUD}, EvidenceType.TOPOLOGY))
async def aws_rds_instance(
    identifier: str, runtime: ToolRuntime[RunContext], marker: str | None = None
) -> tuple[str, Any]:
    """Read one RDS instance and its configuration.

    Args:
        identifier: Exact DB instance identifier.
        marker: Optional Marker returned by a previous page.
    """
    return _result(await _client(runtime).rds_instance(identifier, marker))


@integration_tool(_meta({Domain.CLOUD, Domain.CHANGES}, EvidenceType.EVENTS))
async def aws_rds_events(
    identifier: str,
    start: int,
    end: int,
    runtime: ToolRuntime[RunContext],
    marker: str | None = None,
) -> tuple[str, Any]:
    """Read recent RDS instance events.

    Args:
        identifier: Exact DB instance identifier.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        marker: Optional Marker returned by a previous page.
    """
    return _result(await _client(runtime).rds_events(identifier, start, end, marker))


@integration_tool(_meta({Domain.CLOUD, Domain.METRICS}, EvidenceType.METRICS))
async def aws_rds_performance(
    resource_id: str,
    start: int,
    end: int,
    runtime: ToolRuntime[RunContext],
    next_token: str | None = None,
) -> tuple[str, Any]:
    """Read RDS Performance Insights database load.

    Args:
        resource_id: RDS DbiResourceId or DB instance identifier; names resolve to the resource id.
        start: Start in Unix seconds.
        end: End in Unix seconds.
        next_token: Optional pagination_cursor returned by a previous page.
    """
    return _page_result(await _client(runtime).rds_performance(resource_id, start, end, next_token))


@integration_tool(_meta({Domain.CLOUD}, EvidenceType.TOPOLOGY))
async def aws_lambda_configuration(name: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """Read Lambda function configuration without a signed code URL.

    Args:
        name: Exact function name or ARN.
    """
    return _result(await _client(runtime).lambda_configuration(name))


@integration_tool(_meta({Domain.CLOUD, Domain.CHANGES}, EvidenceType.CHANGES))
async def aws_cloudtrail_events(
    start: int,
    end: int,
    runtime: ToolRuntime[RunContext],
    limit: int = 50,
    next_token: str | None = None,
) -> tuple[str, Any]:
    """Read one page of CloudTrail management events.

    Args:
        start: Start in Unix seconds.
        end: End in Unix seconds.
        limit: Maximum events, up to 50.
        next_token: Optional pagination_cursor returned by a previous page.
    """
    return _page_result(await _client(runtime).cloudtrail_events(start, end, limit, next_token))


@integration_tool(_meta({Domain.CLOUD}, EvidenceType.TOPOLOGY))
async def aws_ec2_instances_by_tag(
    tag_key: str, tag_value: str, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """List one bounded page of EC2 instances with an exact tag.

    Args:
        tag_key: EC2 tag key.
        tag_value: Exact EC2 tag value.
    """
    return _result(await _client(runtime).ec2_instances_by_tag(tag_key, tag_value))


@integration_tool(_meta({Domain.CLOUD}, EvidenceType.TOPOLOGY))
async def aws_elb_target_health(
    target_group_arn: str, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """Read registered target health for one ELB target group.

    Args:
        target_group_arn: Exact ELB target group ARN.
    """
    return _result(await _client(runtime).elb_target_health(target_group_arn))


TOOLS: list[BaseTool] = [
    aws_alarms,
    aws_metric_data,
    aws_logs_start,
    aws_logs_results,
    aws_eks_cluster,
    aws_rds_instance,
    aws_rds_events,
    aws_rds_performance,
    aws_lambda_configuration,
    aws_cloudtrail_events,
    aws_ec2_instances_by_tag,
    aws_elb_target_health,
]
