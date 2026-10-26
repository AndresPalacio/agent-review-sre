"""GitHub registry adapter."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from fin_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from fin_graph.integrations.github.client import GitHubClient
from fin_graph.integrations.github.settings import GitHubSettings
from fin_graph.integrations.github.tools import TOOLS


class GitHubIntegration(Integration[GitHubSettings, GitHubClient]):
    id = "github"
    display_name = "GitHub"

    def load_settings(self, env_file: str | None) -> GitHubSettings | None:
        settings = GitHubSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: GitHubSettings) -> GitHubClient:
        assert settings.owner and settings.repo and settings.token
        return GitHubClient(
            settings.url,
            settings.owner,
            settings.repo,
            settings.token.get_secret_value(),
            timeout_seconds=settings.request_timeout_seconds,
        )

    def tools(self) -> list[BaseTool]:
        return list(TOOLS)

    async def verify(self, client: GitHubClient) -> VerifyResult:
        await client.repository_read()
        return VerifyResult(self.id, VerifyStatus.OK, "GitHub repository read access confirmed")
