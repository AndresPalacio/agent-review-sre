"""Bounded, read-only AWS SDK operations wrapped for async agents."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any, cast

from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.http import require_window


def _date(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


def _limit(value: int, *, maximum: int = 100) -> None:
    if not 1 <= value <= maximum:
        raise IntegrationError(f"AWS page limit must be 1..{maximum}")


class AwsClient:
    def __init__(self, session: Any, region: str, timeout_seconds: float = 15.0) -> None:
        self.session = session
        self.region = region
        self.config = Config(
            connect_timeout=min(timeout_seconds, 5),
            read_timeout=timeout_seconds,
            retries={"max_attempts": 2, "mode": "standard"},
        )

    async def _call(self, service: str, operation: str, **params: Any) -> dict[str, Any]:
        def invoke() -> Any:
            client = self.session.client(service, region_name=self.region, config=self.config)
            return getattr(client, operation)(**params)

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(invoke), timeout=self.config.read_timeout + 6
            )
        except (BotoCoreError, ClientError, TimeoutError) as exc:
            raise IntegrationError(
                f"AWS {service}.{operation} failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(result, dict):
            raise IntegrationError(f"AWS {service}.{operation} returned no object")
        return cast(
            dict[str, Any],
            json.loads(
                json.dumps(
                    {key: value for key, value in result.items() if key != "ResponseMetadata"},
                    default=str,
                )
            ),
        )

    async def alarms(self, limit: int = 20, next_token: str | None = None) -> dict[str, Any]:
        _limit(limit)
        params: dict[str, Any] = {"NextToken": next_token} if next_token else {}
        return await self._call("cloudwatch", "describe_alarms", MaxRecords=limit, **params)

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
        dimensions = (
            [{"Name": dimension_name, "Value": dimension_value}]
            if dimension_name and dimension_value
            else []
        )
        params: dict[str, Any] = {"NextToken": next_token} if next_token else {}
        return await self._call(
            "cloudwatch",
            "get_metric_data",
            MetricDataQueries=[
                {
                    "Id": "m1",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": namespace,
                            "MetricName": metric_name,
                            "Dimensions": dimensions,
                        },
                        "Period": 60,
                        "Stat": "Average",
                    },
                    "ReturnData": True,
                }
            ],
            StartTime=_date(start),
            EndTime=_date(end),
            MaxDatapoints=100,
            ScanBy="TimestampDescending",
            **params,
        )

    async def logs_start(
        self, log_group: str, query: str, start: int, end: int, limit: int = 50
    ) -> dict[str, Any]:
        require_window(start, end)
        _limit(limit)
        return await self._call(
            "logs",
            "start_query",
            logGroupName=log_group,
            queryString=query,
            startTime=start,
            endTime=end,
            limit=limit,
        )

    async def logs_results(self, query_id: str) -> dict[str, Any]:
        """Wait for a terminal result, so a successful tool result is safe to cache."""
        try:
            async with asyncio.timeout(self.config.read_timeout):
                while True:
                    result = await self._call("logs", "get_query_results", queryId=query_id)
                    status = result.get("status")
                    if status in {"Complete", "Failed", "Cancelled", "Timeout"}:
                        return result
                    if status not in {"Scheduled", "Running"}:
                        raise IntegrationError("AWS Logs Insights query returned an unknown status")
                    await asyncio.sleep(0.5)
        except TimeoutError as exc:
            raise IntegrationError(
                "AWS Logs Insights query is still in progress; retry aws_logs_results"
            ) from exc

    async def eks_cluster(self, name: str) -> dict[str, Any]:
        return await self._call("eks", "describe_cluster", name=name)

    async def rds_instance(self, identifier: str, marker: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"Marker": marker} if marker else {}
        return await self._call(
            "rds", "describe_db_instances", DBInstanceIdentifier=identifier, MaxRecords=20, **params
        )

    async def rds_events(
        self, identifier: str, start: int, end: int, marker: str | None = None
    ) -> dict[str, Any]:
        require_window(start, end)
        params: dict[str, Any] = {"Marker": marker} if marker else {}
        return await self._call(
            "rds",
            "describe_events",
            SourceIdentifier=identifier,
            SourceType="db-instance",
            StartTime=_date(start),
            EndTime=_date(end),
            MaxRecords=50,
            **params,
        )

    async def rds_performance(
        self, resource_id: str, start: int, end: int, next_token: str | None = None
    ) -> dict[str, Any]:
        require_window(start, end)
        if not re.fullmatch(r"db-[A-Z0-9]+", resource_id):
            instance = await self.rds_instance(resource_id)
            entries = instance.get("DBInstances", [])
            if not entries or not entries[0].get("DbiResourceId"):
                raise IntegrationError(
                    f"RDS instance {resource_id} has no Performance Insights resource id"
                )
            resource_id = str(entries[0]["DbiResourceId"])
        params: dict[str, Any] = {"NextToken": next_token} if next_token else {}
        return await self._call(
            "pi",
            "get_resource_metrics",
            ServiceType="RDS",
            Identifier=resource_id,
            MetricQueries=[
                {"Metric": "db.load.avg"},
                {"Metric": "db.load.avg", "GroupBy": {"Group": "db.sql_tokenized", "Limit": 10}},
                {"Metric": "db.load.avg", "GroupBy": {"Group": "db.wait_event", "Limit": 10}},
            ],
            StartTime=_date(start),
            EndTime=_date(end),
            PeriodInSeconds=60,
            MaxResults=25,
            **params,
        )

    async def lambda_configuration(self, name: str) -> dict[str, Any]:
        result = await self._call("lambda", "get_function_configuration", FunctionName=name)
        result.pop("Environment", None)
        return result

    async def cloudtrail_events(
        self, start: int, end: int, limit: int = 50, next_token: str | None = None
    ) -> dict[str, Any]:
        require_window(start, end)
        _limit(limit, maximum=50)
        params: dict[str, Any] = {"NextToken": next_token} if next_token else {}
        return await self._call(
            "cloudtrail",
            "lookup_events",
            StartTime=_date(start),
            EndTime=_date(end),
            MaxResults=limit,
            **params,
        )

    async def ec2_instances_by_tag(self, tag_key: str, tag_value: str) -> dict[str, Any]:
        """Read at most one page of instances with an exact EC2 tag value."""
        if not tag_key or not tag_value or len(tag_key) > 128 or len(tag_value) > 256:
            raise IntegrationError("an exact EC2 tag key and value are required")
        return await self._call(
            "ec2",
            "describe_instances",
            Filters=[{"Name": f"tag:{tag_key}", "Values": [tag_value]}],
            MaxResults=100,
        )

    async def elb_target_health(self, target_group_arn: str) -> dict[str, Any]:
        """Read the health of one exact ELB target group."""
        if not target_group_arn.startswith("arn:") or ":targetgroup/" not in target_group_arn:
            raise IntegrationError("a target group ARN is required")
        return await self._call("elbv2", "describe_target_health", TargetGroupArn=target_group_arn)
