from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from sre_graph.graphs.remediation.graph import ActionCatalog, ActionSpec, make_remediation_graph
from sre_graph.integrations.context import RunContext
from sre_graph.safety.approval import ApprovalPolicy


class ScaleArgs(BaseModel):
    namespace: str = Field(min_length=1)
    replicas: int = Field(ge=0, le=10)


def setup_graph() -> tuple[Any, list[str]]:
    calls: list[str] = []

    async def prepare(args: ScaleArgs, context: RunContext) -> dict[str, Any]:
        calls.append(f"dry:{args.replicas}")
        return {"target": args.namespace, "replicas": args.replicas}

    async def execute(args: ScaleArgs, key: str, context: RunContext) -> dict[str, Any]:
        calls.append(f"write:{args.replicas}:{key}")
        return {"replicas": args.replicas}

    async def verify(args: ScaleArgs, context: RunContext) -> bool:
        calls.append(f"verify:{args.replicas}")
        return True

    catalog = ActionCatalog([ActionSpec("scale", ScaleArgs, prepare, execute, verify)])
    graph = make_remediation_graph(
        catalog, ApprovalPolicy(environment="prod"), checkpointer=InMemorySaver()
    )
    return graph, calls


@pytest.mark.parametrize("choice", ["approve", "reject"])
async def test_action_waits_for_approver_and_reject_never_writes(choice: str) -> None:
    graph, calls = setup_graph()
    config = {"configurable": {"thread_id": choice}}
    paused = await graph.ainvoke(
        {"tool": "scale", "args": {"namespace": "prod", "replicas": 2}, "action_id": "a1"},
        config,
    )
    assert paused["__interrupt__"][0].value["action_requests"][0]["name"] == "scale"
    assert calls == ["dry:2"]

    done = await graph.ainvoke(
        Command(resume={"decisions": [{"type": choice}]}),
        config,
        context=RunContext(actor="operator-1", role="approver"),
    )
    assert done["outcome"] == ("verified" if choice == "approve" else "rejected")
    assert len([call for call in calls if call.startswith("write:")]) == (choice == "approve")


async def test_edit_revalidates_and_dry_runs_before_write() -> None:
    graph, calls = setup_graph()
    config = {"configurable": {"thread_id": "edited"}}
    await graph.ainvoke(
        {"tool": "scale", "args": {"namespace": "prod", "replicas": 2}, "action_id": "a1"},
        config,
    )
    edited = {
        "decisions": [
            {
                "type": "edit",
                "edited_action": {"name": "scale", "args": {"namespace": "prod", "replicas": 3}},
            }
        ]
    }
    done = await graph.ainvoke(
        Command(resume=edited), config, context=RunContext(actor="operator-1", role="approver")
    )
    assert done["outcome"] == "verified"
    assert calls == ["dry:2", "dry:3", "write:3:a1", "verify:3"]


async def test_non_approver_cannot_resume_mutation() -> None:
    graph, calls = setup_graph()
    config = {"configurable": {"thread_id": "forged"}}
    await graph.ainvoke(
        {"tool": "scale", "args": {"namespace": "prod", "replicas": 2}, "action_id": "a1"},
        config,
    )
    with pytest.raises(PermissionError):
        await graph.ainvoke(
            Command(resume={"decisions": [{"type": "approve"}]}),
            config,
            context=RunContext(actor="viewer", role="viewer"),
        )
    assert not any(call.startswith("write:") for call in calls)


async def test_scheduler_actor_can_only_reject_expired_action() -> None:
    graph, calls = setup_graph()
    config = {"configurable": {"thread_id": "expires"}}
    await graph.ainvoke(
        {"tool": "scale", "args": {"namespace": "prod", "replicas": 2}, "action_id": "a1"},
        config,
    )
    context = RunContext(actor="scheduler:expiry", role="internal_rejector")
    with pytest.raises(PermissionError):
        await graph.ainvoke(
            Command(resume={"decisions": [{"type": "approve"}]}), config, context=context
        )
    assert calls == ["dry:2"]

    graph, calls = setup_graph()
    config = {"configurable": {"thread_id": "expires-rejected"}}
    await graph.ainvoke(
        {"tool": "scale", "args": {"namespace": "prod", "replicas": 2}, "action_id": "a2"},
        config,
    )
    done = await graph.ainvoke(
        Command(resume={"decisions": [{"type": "reject"}]}), config, context=context
    )
    assert done["outcome"] == "rejected"
    assert calls == ["dry:2"]


async def test_failed_verification_requests_separate_rollback_approval() -> None:
    calls: list[int] = []

    async def prepare(args: ScaleArgs, context: RunContext) -> dict[str, Any]:
        return {"previous_replicas": 1, "replicas": args.replicas}

    async def execute(args: ScaleArgs, key: str, context: RunContext) -> dict[str, Any]:
        calls.append(args.replicas)
        return {"replicas": args.replicas}

    async def verify(args: ScaleArgs, context: RunContext) -> bool:
        return args.replicas == 1

    def rollback(
        args: ScaleArgs, preview: dict[str, Any], result: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        return "scale", {"namespace": args.namespace, "replicas": preview["previous_replicas"]}

    graph = make_remediation_graph(
        ActionCatalog(
            [ActionSpec("scale", ScaleArgs, prepare, execute, verify, rollback_action=rollback)]
        ),
        ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "rollback"}}
    await graph.ainvoke(
        {"tool": "scale", "args": {"namespace": "prod", "replicas": 2}, "action_id": "a1"},
        config,
    )
    context = RunContext(actor="approver", role="approver")
    rollback_pending = await graph.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}), config, context=context
    )
    assert calls == [2]
    assert (
        rollback_pending["__interrupt__"][0].value["action_requests"][0]["arguments"]["replicas"]
        == 1
    )

    final = await graph.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}), config, context=context
    )
    assert calls == [2, 1]
    assert final["outcome"] == "rolled_back"
