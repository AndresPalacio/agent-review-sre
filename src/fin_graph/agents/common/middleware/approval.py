"""Pause tool calls that need a human decision, using LangChain's human-in-the-loop middleware."""

from __future__ import annotations

from collections.abc import Iterable

from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig
from langchain_core.tools import BaseTool

from fin_graph.domain.tools import read_tool_meta
from fin_graph.safety.approval import ApprovalPolicy


def approval_middleware(
    policy: ApprovalPolicy, tools: Iterable[BaseTool]
) -> HumanInTheLoopMiddleware:
    """A HITL middleware that interrupts exactly the ``tools`` the policy says need approval.

    The interrupt carries LangChain's standard ``action_requests`` /
    ``review_configs`` payload; resume with ``{"decisions": [{"type": ...}]}``.
    """
    interrupt_on: dict[str, bool | InterruptOnConfig] = {}
    for tool in tools:
        meta = read_tool_meta(tool)
        if policy.requires_approval(tool.name, meta):
            level = meta.side_effect_level.value if meta is not None else "undeclared"
            interrupt_on[tool.name] = InterruptOnConfig(
                allowed_decisions=["approve", "edit", "reject"],
                description=f"{tool.name} ({level}) needs approval before it runs",
            )
    return HumanInTheLoopMiddleware(interrupt_on=interrupt_on)
