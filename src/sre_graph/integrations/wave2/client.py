"""Finite async read operations for the named second integration wave."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.wave2 import arguments as a
from sre_graph.integrations.wave2.settings import (
    ArgoCDSettings,
    AzureSettings,
    GitLabSettings,
    HoneycombSettings,
    HttpSettings,
    JiraSettings,
    SearchSettings,
    SplunkSettings,
    VictoriaLogsSettings,
)
from sre_graph.integrations.wave2.transport import Wave2Transport
from sre_graph.safety.secrets import redact_secrets

OPERATIONS: dict[str, dict[str, type[a.Arguments]]] = {
    "opsgenie": {"alerts": a.Page, "alert": a.Identifier, "alert_logs": a.DetailPage},
    "incident_io": {"incidents": a.Page, "incident": a.Identifier, "updates": a.DetailPage},
    "jira": {"issues": a.IssueSearch, "issue": a.Identifier},
    "honeycomb": {"traces": a.Traces},
    "elasticsearch": {"logs": a.Logs, "health": a.Arguments},
    "opensearch": {"logs": a.Logs, "health": a.Arguments},
    "splunk": {"logs": a.Logs},
    "victoria_logs": {"logs": a.Logs},
    "coralogix": {"logs": a.Logs},
    "azure": {"logs": a.Logs},
    "argocd": {"applications": a.Page, "application": a.Identifier, "diff": a.Identifier},
    "gitlab": {
        "commits": a.GitPage,
        "merge_requests": a.GitPage,
        "pipelines": a.GitPage,
        "file": a.FileRead,
    },
}


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IntegrationError("provider returned an invalid object")
    if value.get("error") or value.get("errors") or value.get("status") == "error":
        raise IntegrationError("provider returned an error envelope")
    return value


def _rows(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise IntegrationError("provider returned invalid rows")
    if any(not isinstance(row, dict) for row in value):
        raise IntegrationError("provider returned an invalid row")
    return value


def _iso(moment: int) -> str:
    try:
        return datetime.fromtimestamp(moment, UTC).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        raise IntegrationError("timestamp is outside the supported range") from None


def _literal(value: str) -> str:
    # A quoted literal is never parsed as a pipeline/command.
    return json.dumps(value, ensure_ascii=True)


def sanitize(value: Any, credentials: tuple[str, ...] = ()) -> Any:
    """Remove credential fields, provider secrets and Kubernetes Secret payloads."""
    if isinstance(value, dict):
        secret_resource = value.get("kind") == "Secret"
        sensitive_name = re.search(
            r"password|passwd|secret|token|credential|api[_-]?key|private[_-]?key",
            str(value.get("name", "")),
            re.IGNORECASE,
        )
        result: dict[str, Any] = {}
        for key, item in value.items():
            if (
                (secret_resource and key in {"data", "stringData"})
                or (sensitive_name and key == "value")
                or re.search(
                    r"password|passwd|secret|token|credential|api[_-]?key|authorization|private[_-]?key",
                    str(key),
                    re.IGNORECASE,
                )
            ):
                result[key] = "[REDACTED]"
            elif secret_resource and key == "metadata" and isinstance(item, dict):
                # Last-applied annotations can contain the complete Secret document.
                result[key] = {
                    name: sanitize(item[name], credentials)
                    for name in ("name", "namespace")
                    if name in item
                }
            elif key in {"values", "valuesObject"} and isinstance(item, str):
                result[key] = "[inline values omitted]"
            elif key in {
                "liveState",
                "targetState",
                "normalizedLiveState",
                "predictedLiveState",
                "kubectl.kubernetes.io/last-applied-configuration",
            } and isinstance(item, str):
                try:
                    result[key] = sanitize(json.loads(item), credentials)
                except ValueError:
                    result[key] = "[unparsed resource state omitted]"
            else:
                result[key] = sanitize(item, credentials)
        return result
    if isinstance(value, list):
        return [sanitize(item, credentials) for item in value]
    if isinstance(value, str):
        for credential in credentials:
            if credential:
                value = value.replace(credential, "[REDACTED]")
        return redact_secrets(value)
    return value


class Wave2Client:
    def __init__(
        self,
        identity: str,
        settings: HttpSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if identity not in OPERATIONS or not settings.url:
            raise ValueError("invalid wave2 client configuration")
        self.identity = identity
        self.settings = settings
        token = settings.token.get_secret_value() if settings.token else ""
        self.credentials: tuple[str, ...] = (token,)
        headers: dict[str, str] = {}
        if identity == "opsgenie":
            headers["Authorization"] = f"GenieKey {token}"
        elif identity == "honeycomb":
            headers["X-Honeycomb-Team"] = token
        elif identity == "jira":
            assert isinstance(settings, JiraSettings)
            headers["Authorization"] = (
                "Basic " + base64.b64encode(f"{settings.email}:{token}".encode()).decode()
            )
        elif identity in {"elasticsearch", "opensearch"}:
            assert isinstance(settings, SearchSettings)
            if settings.username and settings.password:
                password = settings.password.get_secret_value()
                headers["Authorization"] = (
                    "Basic " + base64.b64encode(f"{settings.username}:{password}".encode()).decode()
                )
                self.credentials = (token, password)
            else:
                headers["Authorization"] = f"ApiKey {token}"
        elif identity == "splunk":
            headers["Authorization"] = f"Splunk {token}"
        elif identity == "gitlab":
            headers["PRIVATE-TOKEN"] = token
        elif token:
            headers["Authorization"] = f"Bearer {token}"
        if isinstance(settings, VictoriaLogsSettings):
            headers.update(
                {"AccountID": str(settings.account_id), "ProjectID": str(settings.project_id)}
            )
        self.http = Wave2Transport(
            settings.url, headers, settings.request_timeout_seconds, transport
        )

    async def call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        schema = OPERATIONS[self.identity].get(operation)
        if schema is None:
            raise IntegrationError("unsupported read operation")
        try:
            args = schema.model_validate(kwargs)
        except ValidationError:
            raise IntegrationError(
                "invalid read arguments; check identifiers, window and row limits"
            ) from None
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                result = await self._read(operation, args.model_dump())
            return sanitize(result, self.credentials)  # type: ignore[no-any-return]
        except (ValidationError, KeyError, TypeError, RecursionError):
            raise IntegrationError("provider returned an invalid response structure") from None
        except TimeoutError:
            raise IntegrationError("read deadline exceeded") from None

    def _result(
        self,
        rows: list[Any],
        limit: int,
        *,
        cursor: Any = None,
        truncated: bool | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        if cursor is not None and not isinstance(cursor, (str, int)):
            raise IntegrationError("provider returned an invalid cursor")
        return {
            "source": self.identity,
            "items": rows[:limit],
            "truncated": bool(cursor) or len(rows) >= limit if truncated is None else truncated,
            "next_cursor": str(cursor) if cursor else None,
            **extra,
        }

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return await self.http.json_read(method, path, **kwargs)

    async def _read(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        identity = self.identity
        limit = args.get("limit", 100)
        cursor = args.get("cursor")
        identifier = quote(args.get("identifier", ""), safe="")
        if identity == "opsgenie":
            if operation == "alerts":
                if cursor is not None and not cursor.isdecimal():
                    raise IntegrationError("Opsgenie cursor must be an offset")
                offset = int(cursor or 0)
                if offset > 100000:
                    raise IntegrationError("Opsgenie offset exceeds the supported range")
                data = _object(
                    await self._json(
                        "GET",
                        "/v2/alerts",
                        params={
                            "limit": limit,
                            "offset": offset,
                            "sort": "createdAt",
                            "order": "desc",
                        },
                    )
                )
                rows = _rows(data.get("data"))
                paging = _object(data.get("paging", {}))
                return self._result(
                    rows, limit, cursor=offset + limit if paging.get("next") else None
                )
            suffix = "/logs" if operation == "alert_logs" else ""
            if cursor:
                raise IntegrationError("alert log cursors are not supported")
            data = _object(
                await self._json(
                    "GET",
                    f"/v2/alerts/{identifier}{suffix}",
                    params={"limit": limit} if suffix else None,
                )
            )
            rows = _rows(data.get("data")) if suffix else [_object(data.get("data"))]
            return self._result(rows, limit, truncated=len(rows) >= limit if suffix else False)
        if identity == "incident_io":
            if operation == "incident":
                data = _object(await self._json("GET", f"/v2/incidents/{identifier}"))
                return self._result([_object(data.get("incident"))], 1, truncated=False)
            path = "/v2/incidents" if operation == "incidents" else "/v2/incident_updates"
            params = {"page_size": limit}
            if operation == "updates":
                params["incident_id"] = args["identifier"]
            if cursor:
                params["after"] = cursor
            data = _object(await self._json("GET", path, params=params))
            field = "incidents" if operation == "incidents" else "incident_updates"
            pagination = _object(data.get("pagination_meta", {}))
            return self._result(_rows(data.get(field)), limit, cursor=pagination.get("after"))
        if identity == "jira":
            if operation == "issue":
                data = _object(
                    await self._json(
                        "GET",
                        f"/rest/api/3/issue/{identifier}",
                        params={"fields": "summary,status,description,created,updated,labels"},
                    )
                )
                return self._result([data], 1, truncated=False)
            body = {
                "jql": args["query"],
                "maxResults": limit,
                "fields": ["summary", "status", "description", "created", "updated", "labels"],
            }
            if cursor:
                body["nextPageToken"] = cursor
            data = _object(await self._json("POST", "/rest/api/3/search/jql", json_body=body))
            return self._result(
                _rows(data.get("issues")),
                limit,
                cursor=data.get("nextPageToken"),
                truncated=not data.get("isLast", not bool(data.get("nextPageToken"))),
            )
        if identity == "honeycomb":
            return await self._honeycomb(args)
        if identity in {"elasticsearch", "opensearch"}:
            if operation == "health":
                data = _object(await self._json("GET", "/_cluster/health"))
                return self._result([data], 1, truncated=False)
            assert isinstance(self.settings, SearchSettings)
            _iso(args["start"])
            _iso(args["end"])
            filters: list[Any] = [
                {
                    "range": {
                        "@timestamp": {
                            "gte": args["start"] * 1000,
                            "lt": args["end"] * 1000,
                            "format": "epoch_millis",
                        }
                    }
                }
            ]
            if args["query"]:
                filters.append({"match_phrase": {"message": args["query"]}})
            body = {
                "size": limit,
                "query": {"bool": {"filter": filters}},
                "sort": [{"@timestamp": "desc"}],
                "timeout": "10s",
                "track_total_hits": False,
            }
            data = _object(
                await self._json(
                    "POST",
                    f"/{quote(self.settings.index or '', safe='*,-')}/_search",
                    json_body=body,
                )
            )
            shards = _object(data.get("_shards", {}))
            if data.get("timed_out") or shards.get("failed", 0):
                raise IntegrationError("search returned incomplete shard results")
            hits = _object(data.get("hits"))
            return self._result(_rows(hits.get("hits")), limit)
        if identity in {"splunk", "victoria_logs", "coralogix"}:
            return await self._logs(args)
        if identity == "azure":
            assert isinstance(self.settings, AzureSettings)
            text = (
                f"{self.settings.table} | where TimeGenerated >= datetime({_iso(args['start'])})"
                f" and TimeGenerated < datetime({_iso(args['end'])})"
            )
            if args["query"]:
                text += f" | where tostring(pack_all()) contains {_literal(args['query'])}"
            text += f" | take {limit}"
            data = _object(
                await self._json(
                    "POST",
                    f"/v1/workspaces/{self.settings.workspace_id}/query",
                    json_body={
                        "query": text,
                        "timespan": f"{_iso(args['start'])}/{_iso(args['end'])}",
                    },
                )
            )
            tables = _rows(data.get("tables"))
            rows = []
            for table in tables:
                columns = _rows(table.get("columns"))
                values = table.get("rows")
                if not isinstance(values, list) or any(
                    not isinstance(row, list) or len(row) != len(columns) for row in values
                ):
                    raise IntegrationError("Azure returned malformed table rows")
                rows.extend(
                    dict(zip((column["name"] for column in columns), row, strict=True))
                    for row in values
                )
            return self._result(rows, limit)
        if identity == "argocd":
            assert isinstance(self.settings, ArgoCDSettings)
            if cursor:
                raise IntegrationError("Argo CD does not supply application page cursors")
            params = {
                key: value
                for key, value in {
                    "project": self.settings.project,
                    "appNamespace": self.settings.namespace,
                }.items()
                if value
            }
            path = "/api/v1/applications"
            if operation != "applications":
                path += f"/{identifier}"
            if operation == "diff":
                path += "/managed-resources"
            data = _object(await self._json("GET", path, params=params))
            if operation == "application":
                return self._result([data], 1, truncated=False)
            rows = _rows(data.get("items", []))
            return self._result(rows, limit)
        if identity == "gitlab":
            return await self._gitlab(operation, args)
        raise IntegrationError("unsupported integration")

    async def _honeycomb(self, args: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(self.settings, HoneycombSettings)
        dataset = quote(self.settings.dataset or "", safe="")
        filters = [
            {"column": column, "op": "=", "value": args[field]}
            for field, column in (("service", "service.name"), ("trace_id", "trace.trace_id"))
            if args[field]
        ]
        _iso(args["start"])
        _iso(args["end"])
        query = {
            "calculations": [{"op": "COUNT"}, {"op": "MAX", "column": "duration_ms"}],
            "breakdowns": ["trace.trace_id", "service.name", "name"],
            "filters": filters,
            "start_time": args["start"],
            "end_time": args["end"],
            "limit": args["limit"],
        }
        created = _object(await self._json("POST", f"/1/queries/{dataset}", json_body=query))
        query_id = a.Identifier.model_validate({"identifier": created.get("id")}).identifier
        result = _object(
            await self._json(
                "POST",
                f"/1/query_results/{dataset}",
                json_body={"query_id": query_id, "disable_series": True},
            )
        )
        result_id = a.Identifier.model_validate({"identifier": result.get("id")}).identifier
        for _ in range(self.settings.poll_attempts):
            if result.get("complete") is True:
                break
            await asyncio.sleep(self.settings.poll_interval_seconds)
            result = _object(
                await self._json("GET", f"/1/query_results/{dataset}/{quote(result_id, safe='')}")
            )
        if result.get("complete") is not True:
            raise IntegrationError("Honeycomb result did not complete within the poll budget")
        data = _object(result.get("data"))
        rows = [_object(item.get("data")) for item in _rows(data.get("results"))]
        return self._result(rows, args["limit"], query_id=query_id, result_id=result_id)

    async def _logs(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = args["limit"]
        start, end = args["start"], args["end"]
        _iso(start)
        _iso(end)
        if self.identity == "splunk":
            assert isinstance(self.settings, SplunkSettings)
            text = f"search index={_literal(self.settings.index or '')}"
            if args["query"]:
                text += " " + _literal(args["query"])
            text += f" | head {limit}"
            chunks = await self.http.stream_read(
                f"/services/search/{self.settings.api_version}/jobs/export",
                form={
                    "search": text,
                    "earliest_time": start,
                    "latest_time": end,
                    "output_mode": "json",
                    "preview": "false",
                },
            )
            rows: list[Any] = []
            for chunk in chunks:
                if chunk.get("error") or any(
                    message.get("type") in {"ERROR", "FATAL"}
                    for message in chunk.get("messages", [])
                ):
                    raise IntegrationError("Splunk export returned an error")
                if chunk.get("preview") is True:
                    continue
                if "result" in chunk:
                    rows.append(_object(chunk["result"]))
                elif "results" in chunk:
                    rows.extend(_rows(chunk["results"]))
                elif not set(chunk) <= {"messages", "lastrow", "preview", "offset"}:
                    raise IntegrationError("Splunk returned an invalid export envelope")
            return self._result(rows, limit)
        if self.identity == "victoria_logs":
            text = _literal(args["query"]) if args["query"] else "*"
            chunks = await self.http.stream_read(
                "/select/logsql/query",
                form={"query": text, "limit": limit, "start": start, "end": end},
            )
            if any(chunk.get("error") for chunk in chunks):
                raise IntegrationError("VictoriaLogs returned an error")
            return self._result(chunks, limit)
        # Quotes/backslashes are escaped within a fixed DataPrime string literal.
        literal = args["query"].replace("\\", "\\\\").replace("'", "\\'")
        text = "source logs"
        if literal:
            text += f" | filter $d.message.contains('{literal}')"
        text += f" | limit {limit}"
        chunks = await self.http.stream_read(
            "/api/v1/dataprime/query",
            body={
                "query": text,
                "metadata": {
                    "startDate": _iso(start),
                    "endDate": _iso(end),
                    "defaultSource": "logs",
                    "syntax": "QUERY_SYNTAX_DATAPRIME",
                    "limit": limit,
                },
            },
        )
        rows = []
        warnings: list[Any] = []
        for chunk in chunks:
            if chunk.get("error") or chunk.get("errorMessage"):
                raise IntegrationError("Coralogix query returned an error")
            if "warning" in chunk:
                warnings.append(chunk["warning"])
            if "result" in chunk:
                result = _object(chunk["result"])
                for item in _rows(result.get("results", [])):
                    user = item.get("userData")
                    if isinstance(user, str):
                        try:
                            user = json.loads(user)
                        except ValueError:
                            raise IntegrationError(
                                "Coralogix returned malformed user data"
                            ) from None
                    rows.append(_object(user) if user is not None else item)
            elif not set(chunk) <= {"warning", "queryId", "metadata", "stats"}:
                raise IntegrationError("Coralogix returned an invalid stream envelope")
        return self._result(rows, limit, warnings=warnings)

    async def _gitlab(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        assert isinstance(self.settings, GitLabSettings)
        root = f"/api/v4/projects/{quote(self.settings.project or '', safe='')}"
        if operation == "file":
            data = _object(
                await self._json(
                    "GET",
                    f"{root}/repository/files/{quote(args['path'], safe='')}",
                    params={"ref": args["ref"]},
                )
            )
            if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
                raise IntegrationError("GitLab returned invalid file content")
            try:
                content = base64.b64decode(data["content"], validate=True).decode("utf-8")
            except (ValueError, UnicodeError):
                raise IntegrationError("GitLab file is not valid UTF-8/base64") from None
            if len(content) > 100000:
                raise IntegrationError("GitLab file exceeds the text limit")
            return self._result(
                [
                    {
                        "path": args["path"],
                        "ref": args["ref"],
                        "content": content,
                        "blob_id": data.get("blob_id"),
                    }
                ],
                1,
                truncated=False,
            )
        cursor = args.get("cursor")
        if cursor and (not cursor.isdecimal() or not 1 <= int(cursor) <= 1000):
            raise IntegrationError("GitLab cursor must be a bounded page number")
        page = int(cursor or 1)
        params: dict[str, Any] = {"per_page": args["limit"], "page": page}
        start, end = args.get("start"), args.get("end")
        if start is not None:
            keys = (
                ("since", "until")
                if operation == "commits"
                else ("updated_after", "updated_before")
            )
            assert end is not None
            params.update({keys[0]: _iso(start), keys[1]: _iso(end)})
        paths = {
            "commits": "/repository/commits",
            "merge_requests": "/merge_requests",
            "pipelines": "/pipelines",
        }
        rows = _rows(await self._json("GET", root + paths[operation], params=params))
        return self._result(
            rows,
            args["limit"],
            cursor=page + 1 if len(rows) >= args["limit"] and page < 1000 else None,
        )

    async def verify(self) -> None:
        """Execute only a bounded read and never create a Honeycomb query for verification."""
        identity = self.identity
        if identity == "honeycomb":
            _object(await self._json("GET", "/1/auth"))
            return
        if identity == "jira":
            _object(await self._json("GET", "/rest/api/3/myself"))
            return
        if identity == "incident_io":
            _object(await self._json("GET", "/v1/identity"))
            return
        operations = {"opsgenie": "alerts", "argocd": "applications", "gitlab": "commits"}
        if identity in {"elasticsearch", "opensearch"}:
            await self.call("health")
        elif identity in operations:
            await self.call(operations[identity], limit=1)
        else:
            end = int(time.time())
            await self.call("logs", query="", start=end - 60, end=end, limit=1)
