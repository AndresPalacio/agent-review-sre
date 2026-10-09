from __future__ import annotations

import httpx
import pytest

from sre_graph.domain.tools import Domain, SideEffectLevel, read_tool_meta
from sre_graph.integrations.base import IntegrationError, VerifyStatus
from sre_graph.integrations.sentry.client import SentryClient
from sre_graph.integrations.sentry.integration import SentryIntegration
from sre_graph.integrations.sentry.settings import SentrySettings


def test_sentry_needs_org_and_token_only_when_enabled() -> None:
    assert SentryIntegration().load_settings(None) is None
    with pytest.raises(ValueError, match="organization"):
        SentrySettings(_env_file=None, enabled=True, token="secret")
    with pytest.raises(ValueError, match="token"):
        SentrySettings(_env_file=None, enabled=True, organization="acme")


async def test_sentry_issues_keep_next_cursor_and_window() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[{"id": "123"}],
            headers={
                "Link": (
                    "<https://sentry.io/api/0/organizations/acme/issues/?cursor=next%3A1>; "
                    'rel="next"; results="true"'
                )
            },
        )

    client = SentryClient(
        "https://sentry.io", "acme", "secret", transport=httpx.MockTransport(respond)
    )
    page = await client.issues("is:unresolved", 1_728_000_000, 1_728_003_600, cursor="first")

    assert page == {"items": [{"id": "123"}], "nextCursor": "next:1"}
    assert seen[0].url.path == "/api/0/organizations/acme/issues/"
    assert seen[0].url.params["cursor"] == "first"
    assert seen[0].url.params["start"] == "2024-10-04T00:00:00+00:00"
    assert seen[0].headers["Authorization"] == "Bearer secret"


async def test_sentry_reads_issue_events_and_releases() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith(("events/", "releases/")):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("events/evt-1/"):
            return httpx.Response(
                200, json={"eventID": "evt-1", "entries": [{"type": "exception"}]}
            )
        return httpx.Response(200, json={"id": "123"})

    client = SentryClient(
        "https://sentry.io", "acme", "secret", transport=httpx.MockTransport(respond)
    )
    assert await client.issue_detail("123") == {"id": "123"}
    assert (await client.events("123", 100, 200))["items"] == []
    assert (await client.event_detail("123", "evt-1"))["entries"] == [{"type": "exception"}]
    assert (await client.releases())["items"] == []
    assert [item.url.path for item in seen] == [
        "/api/0/organizations/acme/issues/123/",
        "/api/0/organizations/acme/issues/123/events/",
        "/api/0/organizations/acme/issues/123/events/evt-1/",
        "/api/0/organizations/acme/releases/",
    ]
    assert seen[1].url.params["start"] == "1970-01-01T00:01:40+00:00"
    assert seen[1].url.params["end"] == "1970-01-01T00:03:20+00:00"


async def test_sentry_rejects_untrusted_issue_path_and_oversized_window() -> None:
    client = SentryClient(
        "https://sentry.io",
        "acme",
        "secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    with pytest.raises(IntegrationError, match="identifier"):
        await client.issue_detail("../admin")
    with pytest.raises(IntegrationError, match="window"):
        await client.issues("", 1, 604_803)


async def test_sentry_verify_reads_organization() -> None:
    client = SentryClient(
        "https://sentry.io",
        "acme",
        "secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"slug": "acme"})),
    )
    assert (await SentryIntegration().verify(client)).status is VerifyStatus.OK


async def test_sentry_rejects_success_status_error_envelope_safely() -> None:
    client = SentryClient(
        "https://sentry.io",
        "acme",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"errors": ["private-remote-detail"]})
        ),
    )
    with pytest.raises(IntegrationError) as caught:
        await client.issues("", 1_728_000_000, 1_728_003_600)
    assert "private-remote-detail" not in str(caught.value)


def test_sentry_tools_route_to_errors_and_changes() -> None:
    tools = SentryIntegration().tools()
    assert len(tools) == 5
    assert Domain.ERRORS in read_tool_meta(tools[0]).domains
    assert Domain.CHANGES in read_tool_meta(tools[-1]).domains
    assert all(
        read_tool_meta(tool).side_effect_level is SideEffectLevel.READ_ONLY for tool in tools
    )
