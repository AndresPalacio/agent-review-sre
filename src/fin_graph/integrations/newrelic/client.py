"""Fixed NerdGraph read queries with variables and checked error envelopes."""

from __future__ import annotations

import re
from typing import Any, cast

import httpx

from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.http import HttpJsonClient, require_window

_SELECT = re.compile(r"^SELECT\b", re.IGNORECASE)
_SERVICE = re.compile(r"[A-Za-z0-9_./:-]{1,120}")
_NRQL = """query($accountId: Int!, $nrqlQuery: Nrql!) {
  actor { account(id: $accountId) { nrql(query: $nrqlQuery) { results } } }
}"""
_ISSUES = """query($accountId: Int!, $cursor: String) {
  actor { account(id: $accountId) { aiIssues { issues(cursor: $cursor) {
    issues { issueId priority state title updatedAt } nextCursor
  } } } }
}"""
_INCIDENTS = """query($accountId: Int!, $cursor: String) {
  actor { account(id: $accountId) { aiIssues { incidents(cursor: $cursor) {
    incidents { incidentId priority state title updatedAt } nextCursor
  } } } }
}"""
_ENTITIES = """query($search: String!, $cursor: String) {
  actor { entitySearch(query: $search) { results(cursor: $cursor) {
    entities { guid name entityType } nextCursor
  } } }
}"""
_ACCOUNT = """query($accountId: Int!) {
  actor { account(id: $accountId) { id name } }
}"""


class NewRelicClient:
    def __init__(
        self,
        url: str,
        api_key: str,
        account_id: int,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.http = HttpJsonClient(url, {"API-Key": api_key}, timeout_seconds, transport)
        self.account_id = account_id

    async def _graphql(
        self, query: str, variables: dict[str, Any], *, experimental: bool = False
    ) -> dict[str, Any]:
        headers = {"nerd-graph-unsafe-experimental-opt-in": "AiIssues"} if experimental else None
        payload = await self.http.request(
            "POST", "/graphql", json_body={"query": query, "variables": variables}, headers=headers
        )
        if (
            not isinstance(payload, dict)
            or payload.get("errors")
            or not isinstance(payload.get("data"), dict)
        ):
            raise IntegrationError("New Relic GraphQL returned an error envelope")
        return cast(dict[str, Any], payload["data"])

    async def _account(
        self, query: str, variables: dict[str, Any], *, experimental: bool = False
    ) -> dict[str, Any]:
        data = await self._graphql(
            query, {"accountId": self.account_id, **variables}, experimental=experimental
        )
        account = (
            data.get("actor", {}).get("account") if isinstance(data.get("actor"), dict) else None
        )
        if not isinstance(account, dict):
            raise IntegrationError("New Relic GraphQL account is unavailable")
        return account

    async def nrql(self, query: str) -> list[dict[str, Any]]:
        expression = query.strip()
        if not _SELECT.match(expression) or ";" in expression or len(expression) > 4000:
            raise IntegrationError("New Relic NRQL must be one SELECT query up to 4000 characters")
        account = await self._account(_NRQL, {"nrqlQuery": expression})
        nrql = account.get("nrql")
        if (
            not isinstance(nrql, dict)
            or not isinstance(nrql.get("results"), list)
            or any(not isinstance(item, dict) for item in nrql["results"])
        ):
            raise IntegrationError("New Relic NRQL response has no results")
        return cast(list[dict[str, Any]], nrql["results"])

    async def issues(self, cursor: str | None = None) -> dict[str, Any]:
        account = await self._account(_ISSUES, {"cursor": cursor}, experimental=True)
        return _issue_page(account, "issues")

    async def incidents(self, cursor: str | None = None) -> dict[str, Any]:
        account = await self._account(_INCIDENTS, {"cursor": cursor}, experimental=True)
        return _issue_page(account, "incidents")

    async def entities(self, search: str, cursor: str | None = None) -> dict[str, Any]:
        data = await self._graphql(_ENTITIES, {"search": search, "cursor": cursor})
        actor = data.get("actor")
        result = actor.get("entitySearch") if isinstance(actor, dict) else None
        page = result.get("results") if isinstance(result, dict) else None
        if (
            not isinstance(page, dict)
            or not isinstance(page.get("entities"), list)
            or any(not isinstance(item, dict) for item in page["entities"])
            or not isinstance(page.get("nextCursor"), str | None)
        ):
            raise IntegrationError("New Relic entity search response has no valid page")
        return {"entities": page["entities"], "nextCursor": page.get("nextCursor")}

    async def apm_errors(
        self, service: str, start: int, end: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        require_window(start, end)
        if not 1 <= limit <= 100:
            raise IntegrationError("New Relic error limit must be 1..100")
        if service and not _SERVICE.fullmatch(service):
            raise IntegrationError("New Relic service name has unsupported characters")
        where = f" WHERE appName = '{service}'" if service else ""
        return await self.nrql(
            f"SELECT * FROM TransactionError{where} SINCE {start * 1000} "
            f"UNTIL {end * 1000} LIMIT {limit}"
        )

    async def account(self) -> dict[str, Any]:
        return await self._account(_ACCOUNT, {})


def _issue_page(account: dict[str, Any], kind: str) -> dict[str, Any]:
    ai_issues = account.get("aiIssues")
    page = ai_issues.get(kind) if isinstance(ai_issues, dict) else None
    if not isinstance(page, dict) or not isinstance(page.get(kind), list):
        raise IntegrationError(f"New Relic {kind} response has no list")
    return page
