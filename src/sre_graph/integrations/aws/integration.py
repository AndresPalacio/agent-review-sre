"""AWS registry adapter."""

from __future__ import annotations

import boto3  # type: ignore[import-untyped]
from langchain_core.tools import BaseTool

from sre_graph.integrations.aws.client import AwsClient
from sre_graph.integrations.aws.settings import AwsSettings
from sre_graph.integrations.aws.tools import TOOLS
from sre_graph.integrations.base import Integration, VerifyResult, VerifyStatus


class AwsIntegration(Integration[AwsSettings, AwsClient]):
    id = "aws"
    display_name = "AWS"

    def load_settings(self, env_file: str | None) -> AwsSettings | None:
        settings = AwsSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: AwsSettings) -> AwsClient:
        assert settings.region is not None
        session = boto3.Session(profile_name=settings.profile, region_name=settings.region)
        return AwsClient(session, settings.region, settings.request_timeout_seconds)

    def tools(self) -> list[BaseTool]:
        return list(TOOLS)

    async def verify(self, client: AwsClient) -> VerifyResult:
        await client.alarms(1)
        return VerifyResult(self.id, VerifyStatus.OK, "CloudWatch alarms read access confirmed")
