"""Explicit native wave2 adapters; shared internals retain finite vendor tools."""

from __future__ import annotations

from typing import Any, cast

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import ConfigDict, create_model
from pydantic_settings import BaseSettings

from fin_graph.domain.tools import Domain, EvidenceType, ToolMeta, attach_tool_meta
from fin_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from fin_graph.integrations.context import RunContext, require_client
from fin_graph.integrations.payload import tool_payload
from fin_graph.integrations.wave2.client import OPERATIONS, Wave2Client
from fin_graph.integrations.wave2.helm import HELM_OPERATIONS, HelmClient
from fin_graph.integrations.wave2.settings import WAVE2_SETTINGS, HelmSettings, HttpSettings

DOMAINS: dict[str, tuple[frozenset[Domain], EvidenceType]] = {
    "opsgenie": (frozenset({Domain.ERRORS, Domain.CHANGES}), EvidenceType.EVENTS),
    "incident_io": (frozenset({Domain.ERRORS, Domain.CHANGES}), EvidenceType.EVENTS),
    "jira": (frozenset({Domain.CHANGES}), EvidenceType.CHANGES),
    "honeycomb": (frozenset({Domain.TRACES}), EvidenceType.TRACES),
    "elasticsearch": (frozenset({Domain.LOGS}), EvidenceType.LOGS),
    "opensearch": (frozenset({Domain.LOGS}), EvidenceType.LOGS),
    "splunk": (frozenset({Domain.LOGS}), EvidenceType.LOGS),
    "victoria_logs": (frozenset({Domain.LOGS}), EvidenceType.LOGS),
    "coralogix": (frozenset({Domain.LOGS}), EvidenceType.LOGS),
    "azure": (frozenset({Domain.LOGS, Domain.CLOUD}), EvidenceType.LOGS),
    "argocd": (frozenset({Domain.CHANGES, Domain.KUBERNETES}), EvidenceType.CHANGES),
    "helm": (frozenset({Domain.CHANGES, Domain.KUBERNETES}), EvidenceType.CHANGES),
    "gitlab": (frozenset({Domain.CHANGES}), EvidenceType.CHANGES),
}

DESCRIPTIONS = {
    "alerts": "Read one bounded page of alerts, newest first.",
    "alert": "Read an alert's details.",
    "alert_logs": "Read bounded alert timeline logs.",
    "incidents": "Read one bounded page of incidents.",
    "incident": "Read incident details.",
    "updates": "Read bounded updates for an incident.",
    "issues": "Search one bounded page of Jira Cloud issues using JQL.",
    "issue": "Read Jira Cloud issue details.",
    "traces": (
        "Query bounded Honeycomb trace groups filtered by service or trace ID. "
        "Creates analytical query/result resources and polls within a fixed budget."
    ),
    "logs": (
        "Search logs for literal text in a bounded time window. "
        "Query programs and pipelines are not accepted."
    ),
    "health": "Read cluster health; no cluster settings are changed.",
    "applications": "Read bounded Argo CD applications; no refresh or synchronization.",
    "application": "Read Argo CD application state and deployment history.",
    "diff": (
        "Read Argo CD managed resource live/target state for drift inspection. "
        "Secret contents are removed."
    ),
    "commits": "Read one bounded page of project commits.",
    "merge_requests": "Read one bounded page of project merge requests.",
    "pipelines": "Read one bounded page of project pipelines.",
    "file": "Read a bounded UTF-8 repository file at a reference.",
    "releases": "Read bounded Helm releases in the configured namespace.",
    "status": "Read Helm release status.",
    "history": "Read bounded Helm release history.",
    "values": "Read Helm values with credential fields redacted.",
    "manifest": "Read Helm manifest resources with Kubernetes Secret data removed.",
}


def _tool(identity: str, operation: str) -> BaseTool:
    """Create a typed operation tool; runtime is injected and absent from agent schema."""
    schemas = HELM_OPERATIONS if identity == "helm" else OPERATIONS[identity]
    args_schema = create_model(
        f"{identity}_{operation}_arguments",
        __base__=schemas[operation],
        __config__=ConfigDict(arbitrary_types_allowed=True),
        runtime=(ToolRuntime[RunContext], ...),
    )

    async def read(runtime: ToolRuntime[RunContext], **kwargs: Any) -> tuple[str, Any]:
        client: HelmClient | Wave2Client
        if identity == "helm":
            client = require_client(runtime, identity, HelmClient)
        else:
            client = require_client(runtime, identity, Wave2Client)
        data = await client.call(operation, **kwargs)
        return tool_payload(data), data

    domains, evidence = DOMAINS[identity]
    if operation == "health":
        domains, evidence = frozenset({Domain.CLOUD}), EvidenceType.TOPOLOGY
    built = StructuredTool.from_function(
        name=f"{identity}_{operation}",
        description=DESCRIPTIONS[operation],
        coroutine=read,
        args_schema=args_schema,
        response_format="content_and_artifact",
    )
    built.handle_tool_error = True
    return attach_tool_meta(
        built,
        ToolMeta(
            integration=identity,
            domains=domains,
            evidence_type=evidence,
            idempotent=identity != "honeycomb",
        ),
    )


class Wave2Integration(Integration[BaseSettings, Wave2Client | HelmClient]):
    def __init__(self) -> None:
        identity = self.id
        schemas = HELM_OPERATIONS if identity == "helm" else OPERATIONS[identity]
        self._tools = [_tool(identity, operation) for operation in schemas]

    def load_settings(self, env_file: str | None) -> BaseSettings | None:
        settings = WAVE2_SETTINGS[self.id](_env_file=env_file)
        assert isinstance(settings, (HttpSettings, HelmSettings))
        return settings if settings.enabled else None

    def build_client(self, settings: BaseSettings) -> Wave2Client | HelmClient:
        if self.id == "helm":
            return HelmClient(cast(HelmSettings, settings))
        return Wave2Client(self.id, cast(HttpSettings, settings))

    def tools(self) -> list[BaseTool]:
        return list(self._tools)

    async def verify(self, client: Wave2Client | HelmClient) -> VerifyResult:
        await client.verify()
        return VerifyResult(self.id, VerifyStatus.OK, "bounded read access confirmed")


def wave2_integrations() -> list[Wave2Integration]:
    return [
        type(
            identity.title().replace("_", "") + "Integration",
            (Wave2Integration,),
            {"id": identity, "display_name": identity.replace("_", " ").title()},
        )()
        for identity in WAVE2_SETTINGS
    ]


__all__ = ["WAVE2_SETTINGS", "wave2_integrations"]
