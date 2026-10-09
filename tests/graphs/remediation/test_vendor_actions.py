from __future__ import annotations

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from sre_graph.graphs.remediation.actions import default_action_catalog
from sre_graph.graphs.remediation.graph import make_remediation_graph
from sre_graph.graphs.remediation.vendor_actions import SilenceArgs
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.sentry.client import SentryClient
from sre_graph.safety.approval import ApprovalPolicy


async def test_sentry_action_reads_before_interrupt_and_verifies_after_approval() -> None:
    methods: list[str] = []
    status = "unresolved"

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal status
        methods.append(request.method)
        assert request.url.path == "/api/0/organizations/acme/issues/99/"
        if request.method == "PUT":
            status = "resolved"
        return httpx.Response(200, json={"id": "99", "status": status})

    client = SentryClient(
        "https://sentry.example", "acme", "token", transport=httpx.MockTransport(respond)
    )
    graph = make_remediation_graph(
        default_action_catalog(),
        ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "sentry-action"}}
    context = RunContext(clients={"sentry": client})

    pending = await graph.ainvoke(
        {
            "tool": "sentry_update_issue",
            "args": {"issue_id": "99", "status": "resolved"},
            "action_id": "sentry-action",
        },
        config,
        context=context,
    )
    assert pending["__interrupt__"]
    assert methods == ["GET"]
    result = await graph.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}),
        config,
        context=RunContext(clients={"sentry": client}, actor="approver", role="approver"),
    )
    assert result["outcome"] == "verified"
    assert methods == ["GET", "PUT", "GET"]


def test_silence_matcher_is_validated_before_approval() -> None:
    with pytest.raises(ValidationError):
        SilenceArgs.model_validate(
            {
                "matchers": [{"name": "invalid/name", "value": "x", "isRegex": False}],
                "starts_at": "2026-10-07T12:00:00Z",
                "ends_at": "2026-10-07T13:00:00Z",
                "created_by": "sre",
                "comment": "maintenance",
            }
        )


def test_created_silence_can_propose_expiry_as_separate_action() -> None:
    spec = default_action_catalog().get("alertmanager_create_silence")
    assert spec.rollback_action is not None
    args = spec.args_schema.model_validate(
        {
            "matchers": [{"name": "service", "value": "checkout", "isRegex": False}],
            "starts_at": "2026-10-07T12:00:00Z",
            "ends_at": "2026-10-07T13:00:00Z",
            "created_by": "sre",
            "comment": "maintenance",
        }
    )
    assert spec.rollback_action(args, {}, {"silence_id": "abc"}) == (
        "alertmanager_expire_silence",
        {"silence_id": "abc"},
    )
