"""Approved observability and ticketing actions, each with a read preflight."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from fin_graph.delivery.client import PagerDutyClient
from fin_graph.domain.tools import SideEffectLevel
from fin_graph.graphs.remediation.graph import ActionSpec
from fin_graph.integrations.alertmanager.client import AlertmanagerClient
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.datadog.client import DatadogClient
from fin_graph.integrations.github.client import GitHubClient
from fin_graph.integrations.grafana.client import GrafanaClient
from fin_graph.integrations.sentry.client import SentryClient


class SilenceArgs(BaseModel):
    matchers: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    starts_at: str
    ends_at: str
    created_by: str = Field(min_length=1, max_length=120)
    comment: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def window(self) -> SilenceArgs:
        if any(
            not isinstance(item.get("name"), str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item["name"]) is None
            or not isinstance(item.get("value"), str)
            or not isinstance(item.get("isRegex"), bool)
            for item in self.matchers
        ):
            raise ValueError("invalid silence matchers")
        start = datetime.fromisoformat(self.starts_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(self.ends_at.replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("invalid silence window")
        return self


class ExpireSilenceArgs(BaseModel):
    silence_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class AnnotationArgs(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    time_ms: int = Field(ge=0)
    tags: list[str] = Field(default_factory=list, max_length=20)


class MuteMonitorArgs(BaseModel):
    monitor_id: int = Field(gt=0)
    end: int = Field(gt=0)


class SentryIssueArgs(BaseModel):
    issue_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    status: str = Field(pattern=r"^(resolved|ignored)$")


class GitHubIssueArgs(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(max_length=10000)


class PagerDutyIncidentArgs(BaseModel):
    incident_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    status: str = Field(pattern=r"^(acknowledged|resolved)$")


async def _unverified(args: BaseModel, context: RunContext) -> bool:
    return False


async def _prepare_silence(args: SilenceArgs, context: RunContext) -> dict[str, Any]:
    existing = await context.client("alertmanager", AlertmanagerClient).silences()
    return {"existing_silences": len(existing), "matchers": args.matchers}


async def _create_silence(args: SilenceArgs, key: str, context: RunContext) -> dict[str, Any]:
    silence_id = await context.client("alertmanager", AlertmanagerClient).create_silence(
        args.matchers, args.starts_at, args.ends_at, args.created_by, args.comment
    )
    return {"silence_id": silence_id}


async def _verify_created_silence(
    args: SilenceArgs, result: dict[str, Any], context: RunContext
) -> bool:
    found = await context.client("alertmanager", AlertmanagerClient).silences()
    return any(item.get("id") == result.get("silence_id") for item in found)


def _rollback_silence(
    args: SilenceArgs, prepared: dict[str, Any], result: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    del args, prepared
    return "alertmanager_expire_silence", {"silence_id": result["silence_id"]}


async def _prepare_expire(args: ExpireSilenceArgs, context: RunContext) -> dict[str, Any]:
    found = await context.client("alertmanager", AlertmanagerClient).silences()
    if not any(item.get("id") == args.silence_id for item in found):
        raise ValueError("silence does not exist")
    return {"silence_id": args.silence_id}


async def _expire_silence(args: ExpireSilenceArgs, key: str, context: RunContext) -> dict[str, Any]:
    await context.client("alertmanager", AlertmanagerClient).expire_silence(args.silence_id)
    return {"silence_id": args.silence_id}


async def _verify_expired(args: ExpireSilenceArgs, context: RunContext) -> bool:
    found = await context.client("alertmanager", AlertmanagerClient).silences()
    return not any(
        item.get("id") == args.silence_id and item.get("status", {}).get("state") == "active"
        for item in found
    )


async def _prepare_annotation(args: AnnotationArgs, context: RunContext) -> dict[str, Any]:
    seconds = args.time_ms // 1000
    await context.client("grafana", GrafanaClient).annotations(max(0, seconds - 60), seconds + 60)
    return {"text": args.text, "time_ms": args.time_ms, "tags": args.tags}


async def _create_annotation(args: AnnotationArgs, key: str, context: RunContext) -> dict[str, Any]:
    annotation_id = await context.client("grafana", GrafanaClient).create_annotation(
        args.text, args.time_ms, args.tags
    )
    return {"annotation_id": annotation_id}


async def _verify_annotation(
    args: AnnotationArgs, result: dict[str, Any], context: RunContext
) -> bool:
    item = await context.client("grafana", GrafanaClient).annotation(result["annotation_id"])
    return item.get("id") == result["annotation_id"] and item.get("text") == args.text


async def _prepare_mute(args: MuteMonitorArgs, context: RunContext) -> dict[str, Any]:
    monitor = await context.client("datadog", DatadogClient).monitor_detail(args.monitor_id)
    return {"monitor_id": monitor["id"], "end": args.end}


async def _mute_monitor(args: MuteMonitorArgs, key: str, context: RunContext) -> dict[str, Any]:
    return await context.client("datadog", DatadogClient).mute_monitor(args.monitor_id, args.end)


async def _verify_mute(args: MuteMonitorArgs, context: RunContext) -> bool:
    monitor = await context.client("datadog", DatadogClient).monitor_detail(args.monitor_id)
    options = monitor.get("options") or {}
    silenced = options.get("silenced") if isinstance(options, dict) else None
    return bool(silenced)


async def _prepare_sentry(args: SentryIssueArgs, context: RunContext) -> dict[str, Any]:
    issue = await context.client("sentry", SentryClient).issue_detail(args.issue_id)
    return {"issue_id": issue["id"], "current_status": issue.get("status")}


async def _update_sentry(args: SentryIssueArgs, key: str, context: RunContext) -> dict[str, Any]:
    return await context.client("sentry", SentryClient).update_issue_status(
        args.issue_id, args.status
    )


async def _verify_sentry(args: SentryIssueArgs, context: RunContext) -> bool:
    issue = await context.client("sentry", SentryClient).issue_detail(args.issue_id)
    return issue.get("status") == args.status


async def _prepare_github(args: GitHubIssueArgs, context: RunContext) -> dict[str, Any]:
    repository = await context.client("github", GitHubClient).repository()
    return {"repository": repository["full_name"], "title": args.title}


async def _create_github(args: GitHubIssueArgs, key: str, context: RunContext) -> dict[str, Any]:
    number = await context.client("github", GitHubClient).create_issue(args.title, args.body)
    return {"number": number}


async def _verify_github(
    args: GitHubIssueArgs, result: dict[str, Any], context: RunContext
) -> bool:
    issue = await context.client("github", GitHubClient).issue_detail(result["number"])
    return issue.get("title") == args.title


async def _prepare_pagerduty(args: PagerDutyIncidentArgs, context: RunContext) -> dict[str, Any]:
    incident = await context.client("pagerduty", PagerDutyClient).incident_detail(args.incident_id)
    return {"incident_id": incident["id"], "current_status": incident.get("status")}


async def _update_pagerduty(
    args: PagerDutyIncidentArgs, key: str, context: RunContext
) -> dict[str, Any]:
    status = await context.client("pagerduty", PagerDutyClient).update_incident_status(
        args.incident_id, args.status
    )
    return {"status": status}


async def _verify_pagerduty(args: PagerDutyIncidentArgs, context: RunContext) -> bool:
    incident = await context.client("pagerduty", PagerDutyClient).incident_detail(args.incident_id)
    return incident.get("status") == args.status


def vendor_action_specs() -> list[ActionSpec[Any]]:
    """Only these side-effecting vendor calls may be proposed to a reviewer."""
    return [
        ActionSpec(
            "alertmanager_create_silence",
            SilenceArgs,
            _prepare_silence,
            _create_silence,
            _unverified,
            side_effect_level=SideEffectLevel.EXTERNAL,
            rollback="Expire the created silence after a separate approval.",
            verify_result=_verify_created_silence,
            rollback_action=_rollback_silence,
        ),
        ActionSpec(
            "alertmanager_expire_silence",
            ExpireSilenceArgs,
            _prepare_expire,
            _expire_silence,
            _verify_expired,
            side_effect_level=SideEffectLevel.EXTERNAL,
        ),
        ActionSpec(
            "grafana_create_annotation",
            AnnotationArgs,
            _prepare_annotation,
            _create_annotation,
            _unverified,
            side_effect_level=SideEffectLevel.EXTERNAL,
            verify_result=_verify_annotation,
        ),
        ActionSpec(
            "datadog_mute_monitor",
            MuteMonitorArgs,
            _prepare_mute,
            _mute_monitor,
            _verify_mute,
            side_effect_level=SideEffectLevel.EXTERNAL,
        ),
        ActionSpec(
            "sentry_update_issue",
            SentryIssueArgs,
            _prepare_sentry,
            _update_sentry,
            _verify_sentry,
            side_effect_level=SideEffectLevel.EXTERNAL,
        ),
        ActionSpec(
            "github_create_issue",
            GitHubIssueArgs,
            _prepare_github,
            _create_github,
            _unverified,
            side_effect_level=SideEffectLevel.EXTERNAL,
            verify_result=_verify_github,
        ),
        ActionSpec(
            "pagerduty_update_incident",
            PagerDutyIncidentArgs,
            _prepare_pagerduty,
            _update_pagerduty,
            _verify_pagerduty,
            side_effect_level=SideEffectLevel.EXTERNAL,
        ),
    ]
