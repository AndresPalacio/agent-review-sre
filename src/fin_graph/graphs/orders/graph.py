"""One paper order with a human in the loop: prepare → approve → execute → verify → report.

- ``prepare`` re-checks the hard limits on the current portfolio and quote and simulates the fill.
  A blocked order ends here: nobody is asked to approve something the limits forbid.
- ``approve`` interrupts with the preview (LangChain's HITL payload, so chat, CLI, API and the
  gateways resume it the same way). Approve, reject, or edit the quantity. Only an authenticated
  approver may decide; the expiry job (``scheduler:expiry``) may only reject. A refused
  decision is recorded in ``denial`` and the order is put to a human again.
- ``execute`` places the order inside a LangGraph task. The broker is idempotent by
  ``client_order_id``, so a resumed or replayed run never fills twice.
- ``verify`` checks the ledger. A filled order that fails verification gets its inverse proposed
  as a rollback, which needs a new approval. Outside market hours the order is ``queued``.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.func import task
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from fin_graph.domain.fills import OrderStatus, PaperExecution
from fin_graph.domain.orders import ProposedOrder
from fin_graph.domain.tools import Domain, EvidenceType, SideEffectLevel, ToolMeta
from fin_graph.graphs.orders.desk import OrderDesk, OrderPreview
from fin_graph.integrations.context import RunContext
from fin_graph.safety.approval import ApprovalPolicy

#: The action name approvals, audits and ``AUTO_APPROVE_TOOLS`` refer to.
PLACE_ORDER = "place_paper_order"
ORDER_META = ToolMeta(
    integration="paper_broker",
    domains=frozenset({Domain.PORTFOLIO}),
    evidence_type=EvidenceType.POSITIONS,
    side_effect_level=SideEffectLevel.MUTATING,
    idempotent=True,
)
#: Every outcome an order run can end with.
OUTCOMES = (
    "blocked",
    "rejected",
    "queued",
    "filled",
    "broker_rejected",
    "verification_failed",
    "rolled_back",
    "rollback_rejected",
    "rollback_blocked",
)


class OrderState(TypedDict, total=False):
    order: dict[str, Any]
    preview: dict[str, Any]
    decision: str
    approver: str
    execution: dict[str, Any]
    verified: bool
    outcome: str
    #: Why the last decision was refused (unauthorized or invalid); empty once one is accepted.
    denial: str
    rollback_phase: bool
    original_execution: dict[str, Any]


def approval_request(
    preview: OrderPreview, order: ProposedOrder, denial: str = ""
) -> dict[str, Any]:
    """The HITL interrupt payload for one order; ``edit`` may change only the quantity.

    ``denial`` says why the previous decision on this order was refused, if one was.
    """
    return {
        "action_requests": [
            {
                "name": PLACE_ORDER,
                "arguments": order.model_dump(mode="json"),
                "description": preview.describe(),
            }
        ],
        "review_configs": [
            {"action_name": PLACE_ORDER, "allowed_decisions": ["approve", "edit", "reject"]}
        ],
        "preview": preview.model_dump(mode="json"),
        "denial": denial,
    }


def _context(runtime: Runtime[RunContext]) -> RunContext:
    return runtime.context if isinstance(runtime.context, RunContext) else RunContext()


def make_order_graph(
    desk: OrderDesk,
    policy: ApprovalPolicy,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the order workflow; nothing reaches the broker before a human decision."""

    async def prepare(state: OrderState) -> dict[str, Any]:
        order = ProposedOrder.model_validate(state["order"])
        preview = await desk.prepare(order)
        update: dict[str, Any] = {"preview": preview.model_dump(mode="json")}
        if not preview.allowed:
            update["outcome"] = "rollback_blocked" if state.get("rollback_phase") else "blocked"
        return update

    def route_prepared(state: OrderState) -> str:
        if state.get("outcome") in {"blocked", "rollback_blocked"}:
            return END
        return "approve" if policy.requires_approval(PLACE_ORDER, ORDER_META) else "execute"

    async def approve(state: OrderState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        order = ProposedOrder.model_validate(state["order"])
        preview = OrderPreview.model_validate(state["preview"])
        decision_set = interrupt(approval_request(preview, order, state.get("denial", "")))
        try:
            return await _decide(state, order, decision_set, _context(runtime))
        except (PermissionError, ValueError) as exc:
            # Never raise after interrupt(): LangGraph keeps a failed resume value and replays
            # it to the next resume, so a refused "approve" would run under the next approver's
            # identity. Finishing the step and asking again starts a clean interrupt instead.
            return {"decision": "denied", "denial": f"{type(exc).__name__}: {exc}"}

    async def _decide(
        state: OrderState, order: ProposedOrder, decision_set: Any, context: RunContext
    ) -> dict[str, Any]:
        if not isinstance(decision_set, dict) or len(decision_set.get("decisions", [])) != 1:
            raise ValueError("one decision is required")
        decision = decision_set["decisions"][0]
        choice = decision.get("type") if isinstance(decision, dict) else None
        if context.actor == "scheduler:expiry" and context.role == "internal_rejector":
            if choice != "reject":
                raise PermissionError("the expiry job may only reject")
        elif not policy.allows_approver(context.actor, context.role):
            raise PermissionError("an authenticated approver is required")
        if choice in {"approve", "reject"}:
            return {"decision": choice, "approver": context.actor, "denial": ""}
        if choice != "edit":
            raise ValueError("invalid decision type")
        edited = decision.get("edited_action")
        if not isinstance(edited, dict) or edited.get("name") != PLACE_ORDER:
            raise ValueError("an edit must keep the same order")
        args = edited.get("args")
        quantity = args.get("quantity") if isinstance(args, dict) else None
        if set(args or {}) - {"quantity"} or not isinstance(quantity, int):
            raise ValueError("an edit may only change the integer quantity")
        resized = desk.with_quantity(order, quantity)
        checked = await desk.prepare(resized)
        update: dict[str, Any] = {
            "decision": "edit",
            "approver": context.actor,
            "denial": "",
            "order": resized.model_dump(mode="json"),
            "preview": checked.model_dump(mode="json"),
        }
        if not checked.allowed:
            update["outcome"] = "rollback_blocked" if state.get("rollback_phase") else "blocked"
        return update

    def route_decision(state: OrderState) -> str:
        if state.get("decision") == "denied":
            return "approve"
        if state.get("decision") == "reject":
            return "report"
        return END if state.get("outcome") in {"blocked", "rollback_blocked"} else "execute"

    async def execute(state: OrderState) -> dict[str, Any]:
        @task(name="place_paper_order")  # type: ignore[arg-type]
        async def place(payload: dict[str, Any]) -> dict[str, Any]:
            execution = await desk.execute(ProposedOrder.model_validate(payload))
            return execution.model_dump(mode="json")

        placed: Any = place
        result: dict[str, Any] = await placed(state["order"])
        return {"execution": result}

    async def verify(state: OrderState) -> dict[str, Any]:
        execution = PaperExecution.model_validate(state["execution"])
        if execution.status is not OrderStatus.FILLED:
            return {"verified": False}
        return {"verified": await desk.verify(execution)}

    def report(state: OrderState) -> dict[str, Any]:
        rollback = bool(state.get("rollback_phase"))
        if state.get("decision") == "reject":
            return {"outcome": "rollback_rejected" if rollback else "rejected"}
        execution = PaperExecution.model_validate(state["execution"])
        if execution.status is OrderStatus.QUEUED:
            return {"outcome": "queued"}
        if execution.status is not OrderStatus.FILLED:
            return {"outcome": "broker_rejected"}
        if state.get("verified"):
            return {"outcome": "rolled_back" if rollback else "filled"}
        inverse = None if rollback else desk.rollback_for(execution)
        if inverse is None:
            return {"outcome": "verification_failed"}
        return {
            "outcome": "rollback_pending",
            "original_execution": state["execution"],
            "order": inverse.model_dump(mode="json"),
            "rollback_phase": True,
            "decision": "",
            "verified": False,
        }

    def route_report(state: OrderState) -> str:
        return "prepare" if state.get("outcome") == "rollback_pending" else END

    graph = StateGraph(OrderState, context_schema=RunContext)
    graph.add_node("prepare", prepare)
    graph.add_node("approve", approve)
    graph.add_node("execute", execute)
    graph.add_node("verify", verify)
    graph.add_node("report", report)
    graph.add_edge(START, "prepare")
    graph.add_conditional_edges("prepare", route_prepared, ["approve", "execute", END])
    graph.add_conditional_edges("approve", route_decision, ["approve", "execute", "report", END])
    graph.add_edge("execute", "verify")
    graph.add_edge("verify", "report")
    graph.add_conditional_edges("report", route_report, ["prepare", END])
    return graph.compile(checkpointer=checkpointer)
