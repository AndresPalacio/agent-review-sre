from __future__ import annotations

import httpx
import pytest

from fin_graph.domain.tools import Domain, SideEffectLevel, read_tool_meta
from fin_graph.integrations.base import IntegrationError, VerifyStatus
from fin_graph.integrations.github.client import GitHubClient
from fin_graph.integrations.github.integration import GitHubIntegration
from fin_graph.integrations.github.settings import GitHubSettings


def test_github_requires_repository_and_token_when_enabled() -> None:
    assert GitHubIntegration().load_settings(None) is None
    with pytest.raises(ValueError, match="owner"):
        GitHubSettings(_env_file=None, enabled=True, repo="api", token="key")
    with pytest.raises(ValueError, match="repo"):
        GitHubSettings(_env_file=None, enabled=True, owner="acme", token="key")


async def test_github_deployments_keep_next_page_without_following_link() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[{"id": 1, "created_at": "2024-10-04T00:30:00Z"}],
            headers={
                "Link": '<https://elsewhere.example/repos/acme/api/deployments?page=2>; rel="next"'
            },
        )

    client = GitHubClient(
        "https://api.github.com", "acme", "api", "key", transport=httpx.MockTransport(respond)
    )
    page = await client.deployments(1_728_000_000, 1_728_003_600, limit=30)

    assert page == {
        "items": [{"id": 1, "created_at": "2024-10-04T00:30:00Z"}],
        "nextPage": 2,
    }
    assert seen[0].url.path == "/repos/acme/api/deployments"
    assert seen[0].url.params["per_page"] == "30"
    assert seen[0].headers["Authorization"] == "Bearer key"
    assert seen[0].headers["X-GitHub-Api-Version"] == "2026-03-10"


async def test_github_deployments_filter_page_to_incident_window() -> None:
    client = GitHubClient(
        "https://api.github.com",
        "acme",
        "api",
        "key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=[
                    {"id": 1, "created_at": "2024-10-05T00:00:00Z"},
                    {"id": 2, "created_at": "2024-10-04T00:30:00Z"},
                ],
                headers={"Link": '<https://api.github.com/deployments?page=2>; rel="next"'},
            )
        ),
    )
    result = await client.deployments(1_728_000_000, 1_728_003_600)
    assert [item["id"] for item in result["items"]] == [2]
    assert result["nextPage"] == 2


async def test_github_reads_commits_pulls_runs_and_compare() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json={"workflow_runs": [{"id": 1}], "total_count": 1})
        if "/compare/" in request.url.path:
            return httpx.Response(200, json={"status": "ahead", "commits": []})
        return httpx.Response(200, json=[])

    client = GitHubClient(
        "https://api.github.com", "acme", "api", "key", transport=httpx.MockTransport(respond)
    )
    assert (await client.commits(1_728_000_000, 1_728_003_600))["items"] == []
    assert (await client.pulls(1_728_000_000, 1_728_003_600))["items"] == []
    assert (await client.workflow_runs(1_728_000_000, 1_728_003_600))["items"] == [{"id": 1}]
    assert (await client.compare("main", "release/v1"))["status"] == "ahead"
    assert seen[0].url.params["since"] == "2024-10-04T00:00:00+00:00"
    assert seen[1].url.params["sort"] == "updated"
    assert seen[2].url.params["created"] == "2024-10-04T00:00:00+00:00..2024-10-04T01:00:00+00:00"
    assert "%2F" in str(seen[3].url)


async def test_github_rejects_untrusted_repo_or_compare_ref() -> None:
    with pytest.raises(IntegrationError, match="identifier"):
        GitHubClient("https://api.github.com", "../evil", "repo", "key")
    client = GitHubClient(
        "https://api.github.com",
        "acme",
        "api",
        "key",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    with pytest.raises(IntegrationError, match="ref"):
        await client.compare("main", "../../secret")
    with pytest.raises(IntegrationError, match="window"):
        await client.commits(1, 604_803)


async def test_github_verify_reads_repository() -> None:
    client = GitHubClient(
        "https://api.github.com",
        "acme",
        "api",
        "key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"full_name": "acme/api"})
        ),
    )
    assert (await GitHubIntegration().verify(client)).status is VerifyStatus.OK


async def test_github_rejects_success_status_error_envelope_safely() -> None:
    client = GitHubClient(
        "https://api.github.com",
        "acme",
        "api",
        "key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"errors": ["private-remote-detail"]})
        ),
    )
    with pytest.raises(IntegrationError) as caught:
        await client.deployments(1_728_000_000, 1_728_003_600)
    assert "private-remote-detail" not in str(caught.value)


def test_github_tools_are_five_read_only_change_tools() -> None:
    tools = GitHubIntegration().tools()
    assert len(tools) == 5
    assert all(Domain.CHANGES in read_tool_meta(tool).domains for tool in tools)
    assert all(
        read_tool_meta(tool).side_effect_level is SideEffectLevel.READ_ONLY for tool in tools
    )
