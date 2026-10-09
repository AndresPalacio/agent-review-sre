"""A single explicit action, prepared, approved, executed and verified."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.func import task
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import BaseModel

from sre_graph.domain.tools import Domain, EvidenceType, SideEffectLevel, ToolMeta
from sre_graph.integrations.context import RunContext
from sre_graph.safety.approval import ApprovalPolicy


@dataclass(frozen=True)
class ActionSpec[ArgsT: BaseModel]:
    name: str
    args_schema: type[ArgsT]
    prepare: Callable[[ArgsT, RunContext], Awaitable[dict[str, Any]]]
    execute: Callable[[ArgsT, str, RunContext], Awaitable[dict[str, Any]]]
    verify: Callable[[ArgsT, RunContext], Awaitable[bool]]
    side_effect_level: SideEffectLevel = SideEffectLevel.MUTATING
    rollback: str = ""
    verify_result: Callable[[ArgsT, dict[str, Any], RunContext], Awaitable[bool]] | None = None
    rollback_action: (
        Callable[[ArgsT, dict[str, Any], dict[str, Any]], tuple[str, dict[str, Any]]] | None
    ) = None


class UncertainActionOutcome(RuntimeError):
    """An earlier call may have reached the target; a replay must not write again."""


class ActionLedger(Protocol):
    async def claim_action(
        self, key: str, fingerprint: str, thread_id: str | None = None
    ) -> tuple[str, dict[str, Any] | None]: ...

    async def complete_action(self, key: str, result: dict[str, Any]) -> None: ...


class ActionCatalog:
    def __init__(
        self, actions: Sequence[ActionSpec[Any]], *, ledger: ActionLedger | None = None
    ) -> None:
        self.actions = {action.name: action for action in actions}
        self.ledger = ledger
        if len(self.actions) != len(actions):
            raise ValueError("duplicate action name")

    def get(self, name: str) -> ActionSpec[Any]:
        try:
            return self.actions[name]
        except KeyError as exc:
            raise ValueError("action is not registered") from exc

    async def prepare(self, name: str, args: dict[str, Any], context: RunContext) -> dict[str, Any]:
        spec = self.get(name)
        parsed = spec.args_schema.model_validate(args)
        return await spec.prepare(parsed, context)

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        key: str,
        context: RunContext,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        spec = self.get(name)
        parsed = spec.args_schema.model_validate(args)
        if self.ledger is not None:
            fingerprint = sha256(
                json.dumps([name, parsed.model_dump(mode="json")], sort_keys=True).encode()
            ).hexdigest()
            status, prior = await self.ledger.claim_action(key, fingerprint, thread_id)
            if status == "completed":
                if prior is None:
                    raise UncertainActionOutcome("completed action has no confirmed result")
                return prior
            if status != "new":
                raise UncertainActionOutcome("action outcome is uncertain; reconcile manually")
        result = await spec.execute(parsed, key, context)
        if self.ledger is not None:
            await self.ledger.complete_action(key, result)
        return result

    async def verify(
        self,
        name: str,
        args: dict[str, Any],
        context: RunContext,
        result: dict[str, Any] | None = None,
    ) -> bool:
        spec = self.get(name)
        parsed = spec.args_schema.model_validate(args)
        if spec.verify_result is not None and result is not None:
            return await spec.verify_result(parsed, result, context)
        return await spec.verify(parsed, context)


class RemediationState(TypedDict, total=False):
    tool: str
    args: dict[str, Any]
    action_id: str
    prepared: dict[str, Any]
    decision: str
    approver: str
    result: dict[str, Any]
    verified: bool
    outcome: str
    rollback: str
    rollback_phase: bool
    original_result: dict[str, Any]


def make_remediation_graph(
    catalog: ActionCatalog,
    policy: ApprovalPolicy,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile a workflow that never performs a mutation before a human decision."""

    async def prepare(state: RemediationState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        if not state.get("action_id"):
            raise ValueError("action_id is required")
        context = runtime.context if isinstance(runtime.context, RunContext) else RunContext()
        preview = await catalog.prepare(state["tool"], state["args"], context)
        return {"prepared": preview}

    def needs_approval(state: RemediationState) -> str:
        spec = catalog.get(state["tool"])
        meta = ToolMeta(
            integration="remediation",
            domains=frozenset({Domain.CHANGES}),
            evidence_type=EvidenceType.CHANGES,
            side_effect_level=spec.side_effect_level,
        )
        return "approve" if policy.requires_approval(spec.name, meta) else "execute"

    async def approve(state: RemediationState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        decision_set = interrupt(
            {
                "action_requests": [
                    {
                        "name": state["tool"],
                        "arguments": state["args"],
                        "description": f"Prepared action {state['action_id']}: {state['prepared']}",
                    }
                ],
                "review_configs": [
                    {
                        "action_name": state["tool"],
                        "allowed_decisions": ["approve", "edit", "reject"],
                    }
                ],
            }
        )
        context = runtime.context if isinstance(runtime.context, RunContext) else RunContext()
        if not isinstance(decision_set, dict) or len(decision_set.get("decisions", [])) != 1:
            raise ValueError("one decision is required")
        decision = decision_set["decisions"][0]
        choice = decision.get("type") if isinstance(decision, dict) else None
        internal_expiry = (
            context.actor == "scheduler:expiry" and context.role == "internal_rejector"
        )
        if internal_expiry:
            if choice != "reject":
                raise PermissionError("the expiry actor may only reject")
        elif not policy.allows_approver(context.actor, context.role):
            raise PermissionError("an authenticated approver is required")
        if choice == "reject":
            return {"decision": "reject", "approver": context.actor}
        if choice == "approve":
            return {"decision": "approve", "approver": context.actor}
        if choice != "edit":
            raise ValueError("invalid decision type")
        edited = decision.get("edited_action")
        if not isinstance(edited, dict) or edited.get("name") != state["tool"]:
            raise ValueError("edited action must keep the same registered tool")
        args = edited.get("args")
        if not isinstance(args, dict):
            raise ValueError("edited args must be an object")
        preview = await catalog.prepare(state["tool"], args, context)
        return {"decision": "edit", "approver": context.actor, "args": args, "prepared": preview}

    def after_approval(state: RemediationState) -> str:
        return "report_outcome" if state.get("decision") == "reject" else "execute"

    async def execute(state: RemediationState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        context = runtime.context if isinstance(runtime.context, RunContext) else RunContext()

        @task(name="execute_remediation_action")  # type: ignore[arg-type]
        async def perform(name: str, args: dict[str, Any], key: str) -> dict[str, Any]:
            thread_id = runtime.execution_info.thread_id if runtime.execution_info else None
            return await catalog.execute(name, args, key, context, thread_id=thread_id)

        perform_call: Any = perform
        result: dict[str, Any] = await perform_call(
            state["tool"], state["args"], state["action_id"]
        )
        return {"result": result}

    async def verify_fix(state: RemediationState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        context = runtime.context if isinstance(runtime.context, RunContext) else RunContext()
        return {
            "verified": await catalog.verify(
                state["tool"], state["args"], context, state.get("result")
            )
        }

    def report_outcome(state: RemediationState) -> dict[str, Any]:
        if state.get("decision") == "reject":
            return {"outcome": "rollback_rejected" if state.get("rollback_phase") else "rejected"}
        if state.get("verified"):
            return {"outcome": "rolled_back" if state.get("rollback_phase") else "verified"}
        spec = catalog.get(state["tool"])
        if state.get("rollback_phase") or spec.rollback_action is None:
            return {"outcome": "verification_failed", "rollback": spec.rollback}
        parsed = spec.args_schema.model_validate(state["args"])
        rollback_tool, rollback_args = spec.rollback_action(
            parsed, state["prepared"], state["result"]
        )
        catalog.get(rollback_tool).args_schema.model_validate(rollback_args)
        return {
            "outcome": "rollback_pending",
            "original_result": state["result"],
            "tool": rollback_tool,
            "args": rollback_args,
            "action_id": f"{state['action_id']}:rollback",
            "rollback_phase": True,
            "decision": "",
        }

    def after_outcome(state: RemediationState) -> str:
        return "prepare" if state.get("outcome") == "rollback_pending" else END

    graph = StateGraph(RemediationState, context_schema=RunContext)
    graph.add_node("prepare", prepare)
    graph.add_node("approve", approve)
    graph.add_node("execute", execute)
    graph.add_node("verify_fix", verify_fix)
    graph.add_node("report_outcome", report_outcome)
    graph.add_edge(START, "prepare")
    graph.add_conditional_edges("prepare", needs_approval, ["approve", "execute"])
    graph.add_conditional_edges("approve", after_approval, ["execute", "report_outcome"])
    graph.add_edge("execute", "verify_fix")
    graph.add_edge("verify_fix", "report_outcome")
    graph.add_conditional_edges("report_outcome", after_outcome, ["prepare", END])
    return graph.compile(checkpointer=checkpointer)
