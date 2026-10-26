from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatResult

from fin_graph.agents.factory import make_agent
from fin_graph.agents.middleware.guardrails import GuardrailsMiddleware
from fin_graph.agents.middleware.tool_cache import CACHE_SCOPE_KEY, ToolResultCacheMiddleware
from fin_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.domain.tools import Domain, SideEffectLevel, read_tool_meta
from fin_graph.integrations.aws.client import AwsClient
from fin_graph.integrations.aws.integration import AwsIntegration
from fin_graph.integrations.aws.settings import AwsSettings
from fin_graph.integrations.base import IntegrationError, VerifyStatus
from fin_graph.integrations.context import RunContext


class FakeService:
    def __init__(self, name: str, calls: list[tuple[str, str, dict[str, Any]]]) -> None:
        self.name = name
        self.calls = calls

    def __getattr__(self, operation: str) -> Any:
        def invoke(**kwargs: Any) -> dict[str, Any]:
            self.calls.append((self.name, operation, kwargs))
            if operation == "get_query_results":
                return {"status": "Complete", "results": []}
            return {"operation": operation, "ResponseMetadata": {"HTTPStatusCode": 200}}

        return invoke


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def client(self, service: str, **kwargs: Any) -> FakeService:
        return FakeService(service, self.calls)


def test_aws_is_disabled_and_requires_a_region() -> None:
    assert AwsIntegration().load_settings(None) is None
    with pytest.raises(ValueError, match="region"):
        AwsSettings(_env_file=None, enabled=True, region=None)


async def test_aws_metric_query_uses_bounded_get_metric_data() -> None:
    session = FakeSession()
    client = AwsClient(session, "us-east-1")

    result = await client.metric_data("AWS/RDS", "CPUUtilization", 1_728_000_000, 1_728_003_600)

    assert result == {"operation": "get_metric_data"}
    service, operation, params = session.calls[0]
    assert (service, operation) == ("cloudwatch", "get_metric_data")
    assert params["StartTime"] == datetime.fromtimestamp(1_728_000_000, tz=UTC)
    assert params["EndTime"] == datetime.fromtimestamp(1_728_003_600, tz=UTC)
    assert params["MaxDatapoints"] == 100
    assert params["MetricDataQueries"][0]["MetricStat"]["Metric"]["Namespace"] == "AWS/RDS"


async def test_aws_paginated_reads_accept_continuation_tokens() -> None:
    session = FakeSession()
    client = AwsClient(session, "us-east-1")

    await client.alarms(next_token="alarm-page-2")
    await client.metric_data("AWS/RDS", "CPUUtilization", 100, 200, next_token="metric-page-2")
    await client.rds_instance("orders", marker="instance-page-2")
    await client.rds_events("orders", 100, 200, marker="event-page-2")
    await client.rds_performance("db-ABC", 100, 200, next_token="pi-page-2")
    await client.cloudtrail_events(100, 200, next_token="trail-page-2")

    assert session.calls[0][2]["NextToken"] == "alarm-page-2"
    assert session.calls[1][2]["NextToken"] == "metric-page-2"
    assert session.calls[2][2]["Marker"] == "instance-page-2"
    assert session.calls[3][2]["Marker"] == "event-page-2"
    assert session.calls[4][2]["NextToken"] == "pi-page-2"
    assert session.calls[5][2]["NextToken"] == "trail-page-2"


async def test_aws_logs_query_is_split_into_start_and_bounded_results() -> None:
    session = FakeSession()
    client = AwsClient(session, "us-east-1")

    await client.logs_start("/aws/lambda/api", "fields @message", 100, 200)
    await client.logs_results("query-1")

    assert session.calls[0] == (
        "logs",
        "start_query",
        {
            "logGroupName": "/aws/lambda/api",
            "queryString": "fields @message",
            "startTime": 100,
            "endTime": 200,
            "limit": 50,
        },
    )
    assert session.calls[1] == ("logs", "get_query_results", {"queryId": "query-1"})


class LogsSession(FakeSession):
    def __init__(self, statuses: list[str]) -> None:
        super().__init__()
        self.statuses = iter(statuses)

    def client(self, service: str, **kwargs: Any) -> FakeService:
        statuses = self.statuses

        class LogsService(FakeService):
            def get_query_results(self, **params: Any) -> dict[str, Any]:
                self.calls.append((self.name, "get_query_results", params))
                status = next(statuses, "Running")
                return {
                    "status": status,
                    "results": [[{"field": "@message", "value": "request completed"}]]
                    if status == "Complete"
                    else [],
                    "statistics": {"recordsMatched": 1, "recordsScanned": 2},
                }

        return LogsService(service, self.calls)


@pytest.mark.parametrize("initial_status", ["Scheduled", "Running"])
async def test_logs_agent_caches_only_the_completed_query(initial_status: str) -> None:
    session = LogsSession([initial_status, "Complete"])
    model = ScriptedChatModel(
        responses=[
            tool_call_message("aws_logs_results", {"query_id": "query-1"}, "first"),
            tool_call_message("aws_logs_results", {"query_id": "query-1"}, "second"),
            AIMessage("done"),
        ]
    )
    cache = ToolResultCacheMiddleware()
    agent = make_agent(
        name="cloud",
        model=model,
        tools=AwsIntegration().tools(),
        context_schema=RunContext,
        middleware=[GuardrailsMiddleware(), cache],
    )

    result = await agent.ainvoke(
        {"messages": [HumanMessage("read query results")]},
        {"configurable": {CACHE_SCOPE_KEY: "logs-run"}},
        context=RunContext(clients={"aws": AwsClient(session, "us-east-1")}),
    )

    messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert [message.artifact["status"] for message in messages] == ["Complete", "Complete"]
    assert messages[-1].artifact["results"] == [
        [{"field": "@message", "value": "request completed"}]
    ]
    assert len(session.calls) == 2
    assert cache.hits == 1


async def test_logs_query_still_running_at_deadline_returns_retryable_error() -> None:
    client = AwsClient(LogsSession(["Running"]), "us-east-1", timeout_seconds=0.01)

    with pytest.raises(IntegrationError, match="still in progress"):
        await client.logs_results("query-1")


@pytest.mark.parametrize("status", ["Unknown", "unrecognized"])
async def test_logs_query_unknown_state_is_not_successful_evidence(status: str) -> None:
    client = AwsClient(LogsSession([status]), "us-east-1")

    with pytest.raises(IntegrationError, match="unknown status"):
        await client.logs_results("query-1")


class PagingSession(FakeSession):
    def __init__(self, cursor: str = "aws-page-2") -> None:
        super().__init__()
        self.cursor = cursor

    def client(self, service: str, **kwargs: Any) -> FakeService:
        cursor = self.cursor

        class PagingService(FakeService):
            def __getattr__(self, operation: str) -> Any:
                def invoke(**params: Any) -> dict[str, Any]:
                    self.calls.append((self.name, operation, params))
                    if "NextToken" not in params:
                        return {
                            "Items": [{"page": 1, "NextToken": "nested-auth-secret"}],
                            "NextToken": cursor,
                            "AuthToken": "opaque-auth-secret",
                            "Credentials": "opaque-credentials",
                        }
                    if params["NextToken"] == cursor:
                        return {"Items": [{"page": 2}]}
                    return {"error": "invalid pagination cursor"}

                return invoke

        return PagingService(service, self.calls)


class PagingModel(ScriptedChatModel):
    """Use the cursor the tool actually exposed in the next request."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        result = super()._generate(messages, stop, run_manager, **kwargs)
        response = result.generations[0].message
        if isinstance(response, AIMessage) and response.tool_calls:
            call = response.tool_calls[0]
            if "next_token" in call["args"]:
                content = next(
                    message.content
                    for message in reversed(messages)
                    if isinstance(message, ToolMessage)
                )
                call["args"]["next_token"] = json.loads(content).get(
                    "pagination_cursor", "missing-cursor"
                )
        return result


@pytest.mark.parametrize(
    ("tool_name", "args", "service", "operation"),
    [
        ("aws_alarms", {}, "cloudwatch", "describe_alarms"),
        (
            "aws_metric_data",
            {"namespace": "AWS/RDS", "metric_name": "CPUUtilization", "start": 100, "end": 200},
            "cloudwatch",
            "get_metric_data",
        ),
        (
            "aws_rds_performance",
            {"resource_id": "db-ABC", "start": 100, "end": 200},
            "pi",
            "get_resource_metrics",
        ),
        ("aws_cloudtrail_events", {"start": 100, "end": 200}, "cloudtrail", "lookup_events"),
    ],
)
async def test_aws_agent_reads_second_page_using_cursor_from_guarded_payload(
    tool_name: str, args: dict[str, Any], service: str, operation: str
) -> None:
    session = PagingSession()
    model = PagingModel(
        responses=[
            tool_call_message(tool_name, args, "page-1"),
            tool_call_message(tool_name, {**args, "next_token": "from-result"}, "page-2"),
            AIMessage("done"),
        ]
    )
    agent = make_agent(
        name="cloud",
        model=model,
        tools=AwsIntegration().tools(),
        context_schema=RunContext,
        middleware=[GuardrailsMiddleware(), ToolResultCacheMiddleware()],
    )

    result = await agent.ainvoke(
        {"messages": [HumanMessage("read both pages")]},
        {"configurable": {CACHE_SCOPE_KEY: "paging-run"}},
        context=RunContext(clients={"aws": AwsClient(session, "us-east-1")}),
    )

    messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert json.loads(messages[0].content).get("pagination_cursor") == "aws-page-2"
    assert messages[0].artifact.get("pagination_cursor") == "aws-page-2"
    assert messages[1].artifact == {"Items": [{"page": 2}]}
    assert session.calls[1][:2] == (service, operation)
    assert session.calls[1][2]["NextToken"] == "aws-page-2"
    exposed = str([(message.content, message.artifact) for message in messages])
    for secret in ("nested-auth-secret", "opaque-auth-secret", "opaque-credentials"):
        assert secret not in exposed


@pytest.mark.parametrize(
    "secret", ["AKIA" + "ABCDEFGHIJKLMNOP", "ghp_" + "a" * 36, "Bearer opaque-auth-secret"]
)
async def test_aws_cursor_values_still_pass_credential_format_filters(secret: str) -> None:
    model = ScriptedChatModel(
        responses=[tool_call_message("aws_alarms", {}, "page-1"), AIMessage("done")]
    )
    agent = make_agent(
        name="cloud",
        model=model,
        tools=AwsIntegration().tools(),
        context_schema=RunContext,
        middleware=[GuardrailsMiddleware()],
    )

    result = await agent.ainvoke(
        {"messages": [HumanMessage("read alarms")]},
        context=RunContext(clients={"aws": AwsClient(PagingSession(secret), "us-east-1")}),
    )

    message = next(message for message in result["messages"] if isinstance(message, ToolMessage))
    assert secret not in str((message.content, message.artifact))
    assert "[REDACTED:" in json.loads(message.content).get("pagination_cursor", "")


async def test_aws_reads_cover_cloud_services_with_fixed_operations() -> None:
    session = FakeSession()
    client = AwsClient(session, "us-east-1")

    await client.alarms()
    await client.eks_cluster("prod")
    await client.rds_instance("orders")
    await client.rds_events("orders", 100, 200)
    await client.rds_performance("db-ABC", 100, 200)
    await client.lambda_configuration("worker")
    await client.cloudtrail_events(100, 200)

    assert [(service, operation) for service, operation, _ in session.calls] == [
        ("cloudwatch", "describe_alarms"),
        ("eks", "describe_cluster"),
        ("rds", "describe_db_instances"),
        ("rds", "describe_events"),
        ("pi", "get_resource_metrics"),
        ("lambda", "get_function_configuration"),
        ("cloudtrail", "lookup_events"),
    ]
    assert session.calls[4][2]["MaxResults"] == 25
    pi_queries = session.calls[4][2]["MetricQueries"]
    assert len(pi_queries) == 3
    assert {query["GroupBy"]["Group"] for query in pi_queries[1:]} == {
        "db.sql_tokenized",
        "db.wait_event",
    }
    assert session.calls[-1][2]["MaxResults"] == 50


async def test_performance_resolves_db_name_to_resource_id() -> None:
    class ResolvingSession(FakeSession):
        def client(self, service: str, **kwargs: Any) -> FakeService:
            class ResolvingService(FakeService):
                def describe_db_instances(self, **params: Any) -> dict[str, Any]:
                    self.calls.append((self.name, "describe_db_instances", params))
                    return {"DBInstances": [{"DbiResourceId": "db-ABC"}]}

            return ResolvingService(service, self.calls)

    session = ResolvingSession()
    await AwsClient(session, "us-east-1").rds_performance("payments-prod", 100, 200)
    assert [operation for _, operation, _ in session.calls] == [
        "describe_db_instances",
        "get_resource_metrics",
    ]
    assert session.calls[-1][2]["Identifier"] == "db-ABC"


async def test_ec2_and_elb_topology_reads_use_bounded_filters() -> None:
    session = FakeSession()
    client = AwsClient(session, "us-east-1")

    await client.ec2_instances_by_tag("tier", "web")
    await client.elb_target_health("arn:aws:elasticloadbalancing:us-east-1:123:targetgroup/web/abc")

    assert session.calls[0] == (
        "ec2",
        "describe_instances",
        {"Filters": [{"Name": "tag:tier", "Values": ["web"]}], "MaxResults": 100},
    )
    assert session.calls[1][0:2] == ("elbv2", "describe_target_health")
    assert session.calls[1][2]["TargetGroupArn"].endswith("targetgroup/web/abc")


async def test_lambda_configuration_excludes_environment_values() -> None:
    class LambdaSession(FakeSession):
        def client(self, service: str, **kwargs: Any) -> FakeService:
            class LambdaService(FakeService):
                def get_function_configuration(self, **params: Any) -> dict[str, Any]:
                    return {
                        "FunctionName": "worker",
                        "Environment": {"Variables": {"SERVICE_AUTH": "opaque-private-value"}},
                    }

            return LambdaService(service, self.calls)

    result = await AwsClient(LambdaSession(), "us-east-1").lambda_configuration("worker")
    assert result == {"FunctionName": "worker"}
    assert "opaque-private-value" not in str(result)


async def test_aws_rejects_unbounded_window_before_sdk_call() -> None:
    session = FakeSession()
    client = AwsClient(session, "us-east-1")
    with pytest.raises(IntegrationError, match="window"):
        await client.cloudtrail_events(1, 604_803)
    assert session.calls == []


async def test_aws_verify_is_an_authenticated_cloudwatch_read() -> None:
    session = FakeSession()
    result = await AwsIntegration().verify(AwsClient(session, "us-east-1"))
    assert result.status is VerifyStatus.OK
    assert session.calls[0][1] == "describe_alarms"


def test_aws_tools_are_read_only_and_cover_cloud_metrics_logs_and_changes() -> None:
    tools = AwsIntegration().tools()
    assert len(tools) == 12
    assert {"aws_ec2_instances_by_tag", "aws_elb_target_health"} <= {tool.name for tool in tools}
    domains = set().union(*(read_tool_meta(candidate).domains for candidate in tools))
    assert {Domain.CLOUD, Domain.METRICS, Domain.LOGS, Domain.CHANGES} <= domains
    assert all(
        read_tool_meta(candidate).side_effect_level is SideEffectLevel.READ_ONLY
        for candidate in tools
    )
