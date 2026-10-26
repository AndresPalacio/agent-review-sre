from __future__ import annotations

import json

import httpx
import pytest

from fin_graph.domain.tools import Domain, SideEffectLevel, read_tool_meta
from fin_graph.integrations.base import IntegrationError, VerifyStatus
from fin_graph.integrations.newrelic.client import NewRelicClient
from fin_graph.integrations.newrelic.integration import NewRelicIntegration
from fin_graph.integrations.newrelic.settings import NewRelicSettings


def test_newrelic_is_disabled_and_requires_a_user_key_and_account() -> None:
    assert NewRelicIntegration().load_settings(None) is None
    with pytest.raises(ValueError, match="api_key"):
        NewRelicSettings(_env_file=None, enabled=True)
    with pytest.raises(ValueError, match="account_id"):
        NewRelicSettings(_env_file=None, enabled=True, api_key="secret")
    assert NewRelicSettings(_env_file=None, region="eu").url == "https://api.eu.newrelic.com"


def _client(seen: list[httpx.Request], response: dict[str, object] | None = None) -> NewRelicClient:
    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        payload = response or {
            "data": {
                "actor": {
                    "account": {
                        "nrql": {"results": [{"count": 2}]},
                        "aiIssues": {
                            "issues": {"issues": [], "nextCursor": "next"},
                            "incidents": {"incidents": [], "nextCursor": "next"},
                        },
                        "id": 42,
                        "name": "SRE",
                    },
                    "entitySearch": {"results": {"entities": [], "nextCursor": None}},
                }
            }
        }
        return httpx.Response(200, json=payload)

    return NewRelicClient(
        "https://api.newrelic.com", "secret-key", 42, transport=httpx.MockTransport(respond)
    )


async def test_nrql_uses_graphql_variables_and_returns_results() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    assert await client.nrql("SELECT count(*) FROM Transaction SINCE 1 HOUR AGO") == [{"count": 2}]
    body = json.loads(seen[0].content)
    assert body["variables"] == {
        "accountId": 42,
        "nrqlQuery": "SELECT count(*) FROM Transaction SINCE 1 HOUR AGO",
    }
    assert "nrql(query: $nrqlQuery)" in body["query"]
    assert seen[0].headers["API-Key"] == "secret-key"


async def test_nrql_rejects_non_select_queries() -> None:
    client = _client([])
    for query in [
        "DELETE FROM Transaction",
        "SELECT * FROM Transaction; DELETE FROM Transaction",
        "mutation {x}",
    ]:
        with pytest.raises(IntegrationError, match="SELECT"):
            await client.nrql(query)


async def test_issues_and_incidents_use_experimental_opt_in_and_keep_cursor() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    assert (await client.issues())["nextCursor"] == "next"
    assert (await client.incidents())["nextCursor"] == "next"
    assert all(r.headers["nerd-graph-unsafe-experimental-opt-in"] == "AiIssues" for r in seen)


async def test_entities_and_apm_errors_are_read_queries() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    assert await client.entities("name LIKE 'checkout'") == {"entities": [], "nextCursor": None}
    assert await client.apm_errors("checkout", 1_728_000_000, 1_728_003_600, 10) == [{"count": 2}]
    body = json.loads(seen[1].content)
    assert "TransactionError" in body["variables"]["nrqlQuery"]
    assert "appName = 'checkout'" in body["variables"]["nrqlQuery"]
    assert "SINCE 1728000000000 UNTIL 1728003600000" in body["variables"]["nrqlQuery"]


async def test_entity_search_preserves_and_accepts_a_next_cursor() -> None:
    seen: list[httpx.Request] = []
    response: dict[str, object] = {
        "data": {
            "actor": {
                "entitySearch": {
                    "results": {
                        "entities": [{"guid": "one", "name": "checkout", "entityType": "APM"}],
                        "nextCursor": "second-page",
                    }
                }
            }
        }
    }
    client = _client(seen, response)

    assert await client.entities("name LIKE 'checkout'") == {
        "entities": [{"guid": "one", "name": "checkout", "entityType": "APM"}],
        "nextCursor": "second-page",
    }
    assert await client.entities("name LIKE 'checkout'", "second-page") == {
        "entities": [{"guid": "one", "name": "checkout", "entityType": "APM"}],
        "nextCursor": "second-page",
    }
    first, second = (json.loads(request.content) for request in seen)
    assert "nextCursor" in first["query"]
    assert "results(cursor: $cursor)" in first["query"]
    assert first["variables"] == {"search": "name LIKE 'checkout'", "cursor": None}
    assert second["variables"] == {
        "search": "name LIKE 'checkout'",
        "cursor": "second-page",
    }


async def test_graphql_errors_are_tool_errors() -> None:
    client = _client(
        [], {"data": {"actor": {"account": None}}, "errors": [{"message": "secret-key"}]}
    )
    with pytest.raises(IntegrationError) as caught:
        await client.nrql("SELECT count(*) FROM Transaction")
    assert "GraphQL" in str(caught.value)
    assert "secret-key" not in str(caught.value)


async def test_newrelic_verify_requires_account_read_access() -> None:
    client = _client([])
    assert (await NewRelicIntegration().verify(client)).status is VerifyStatus.OK


def test_newrelic_tools_are_five_readers_with_domains() -> None:
    tools = NewRelicIntegration().tools()
    assert [t.name for t in tools] == [
        "nr_nrql",
        "nr_issues",
        "nr_incidents",
        "nr_entities",
        "nr_apm_errors",
    ]
    assert Domain.ERRORS in read_tool_meta(tools[-1]).domains
    assert all(read_tool_meta(t).side_effect_level is SideEffectLevel.READ_ONLY for t in tools)
