"""Opt-in Agent Server graphs for a bounded, synthetic async equity-analysis pilot.

A supervisor starts at most two children, a fundamentals analyst and a technicals
analyst, that read SYNTHETIC fixtures for a ticker and horizon. Nothing here reads
market data or touches the paper portfolio."""

from __future__ import annotations

import asyncio
import json
import math
import operator
import os
import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from deepagents import create_deep_agent
from deepagents.middleware.async_subagents import AsyncSubAgentMiddleware
from langchain.agents.middleware import AgentMiddleware, AgentState, ModelCallLimitMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain.tools import ToolRuntime
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command
from langgraph_sdk import get_client

from fin_graph.agents.factory import make_agent
from fin_graph.config.settings import Settings
from fin_graph.llm.models import build_chat_model, parse_model_spec
from fin_graph.safety.secrets import redact_value
from fin_graph.work.testing import OfflinePilotModel

Role = Literal["supervisor", "fundamentals", "technicals"]
ToolResult = ToolMessage | Command[Any]
_ASYNC_TOOLS = frozenset(
    {
        "start_async_task",
        "update_async_task",
        "cancel_async_task",
        "check_async_task",
        "list_async_tasks",
    }
)


class PilotContext(TypedDict):
    """Portable context supplied by the gateway, never SDK clients or credentials."""

    actor: str
    work_id: str
    revision: int
    deadline: float
    max_children: NotRequired[int]
    delay_seconds: NotRequired[float]
    ledger_path: str
    consolidating: NotRequired[bool]


def _latest_revision(previous: int, current: int) -> int:
    return max(previous, current)


class PilotState(AgentState):
    decision_trace: Annotated[NotRequired[list[dict[str, Any]]], operator.add]
    work_revision: Annotated[NotRequired[int], _latest_revision]
    result_revision: NotRequired[int]
    model_mode: NotRequired[str]


def _model(role: Role) -> BaseChatModel:
    selected = os.environ.get("FIN_ASYNC_MODEL", "offline").strip()
    if selected == "offline":
        return OfflinePilotModel(role=role)
    try:
        spec = parse_model_spec(selected)
    except ValueError:
        raise ValueError("FIN_ASYNC_MODEL must be 'offline' or 'provider:model'") from None
    return build_chat_model(spec, Settings())


class PilotGuard(AgentMiddleware[PilotState, PilotContext]):
    """Runtime authorization and observable model decisions, for both model modes."""

    state_schema = PilotState

    def __init__(self, *, role: Role, provenance: str) -> None:
        self.role = role
        self.provenance = provenance
        self.allowed_tools = (
            _ASYNC_TOOLS if role == "supervisor" else frozenset({f"read_{role}_fixture"})
        )

    def before_agent(self, state: PilotState, runtime: Runtime[PilotContext]) -> dict[str, Any]:
        ctx = runtime.context or {}
        return {"work_revision": int(ctx.get("revision", 0)), "model_mode": self.provenance}

    async def abefore_agent(
        self, state: PilotState, runtime: Runtime[PilotContext]
    ) -> dict[str, Any]:
        return self.before_agent(state, runtime)

    def after_agent(
        self, state: PilotState, runtime: Runtime[PilotContext]
    ) -> dict[str, Any] | None:
        # Budget middleware can create a final AI message without calling a
        # model. Stamp that answer too, using this run rather than max revision.
        message = state["messages"][-1]
        if isinstance(message, AIMessage) and not message.tool_calls:
            return {"result_revision": int((runtime.context or {}).get("revision", 0))}
        return None

    async def aafter_agent(
        self, state: PilotState, runtime: Runtime[PilotContext]
    ) -> dict[str, Any] | None:
        return self.after_agent(state, runtime)

    def after_model(
        self, state: PilotState, runtime: Runtime[PilotContext]
    ) -> dict[str, Any] | None:
        message = state["messages"][-1]
        if not isinstance(message, AIMessage):
            return None
        if not message.tool_calls:
            return self.after_agent(state, runtime)
        return {
            "decision_trace": [
                {
                    "call_id": call["id"],
                    "tool": call["name"],
                    "args": redact_value(call["args"]),
                    "provenance": self.provenance,
                    "revision": (runtime.context or {}).get("revision", 0),
                }
                for call in message.tool_calls
            ]
        }

    async def aafter_model(
        self, state: PilotState, runtime: Runtime[PilotContext]
    ) -> dict[str, Any] | None:
        return self.after_model(state, runtime)

    def _request(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        if self.role == "supervisor" and (request.runtime.context or {}).get("consolidating"):
            return request.override(tools=[])
        if isinstance(request.model, OfflinePilotModel):
            allowed = (request.runtime.context or {}).get("max_children", 2) != 0
            request = request.override(
                model=request.model.model_copy(update={"allow_delegation": allowed})
            )
        return request.override(
            tools=[
                t
                for t in request.tools
                if (t.name if isinstance(t, BaseTool) else t.get("name")) in self.allowed_tools
            ]
        )

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._request(request))

    def _denial(self, request: ToolCallRequest) -> str | None:
        name, args = request.tool_call["name"], request.tool_call["args"]
        ctx = request.runtime.context
        if self.role == "supervisor" and isinstance(ctx, Mapping) and ctx.get("consolidating"):
            return "consolidation_read_only"
        if name not in self.allowed_tools:
            return "tool_not_allowed"
        if not isinstance(ctx, Mapping) or not all(
            ctx.get(key) for key in ("actor", "work_id", "deadline")
        ):
            return "invalid_context"
        deadline = ctx.get("deadline")
        if not isinstance(deadline, int | float) or not math.isfinite(deadline):
            return "invalid_context"
        # Without the gateway ledger, cancellation and revision cannot be checked.
        path = ctx.get("ledger_path")
        if not isinstance(path, str) or not path:
            return "control_unavailable"
        from fin_graph.work.store import read_control

        try:
            control = read_control(path, ctx["work_id"])
        except (sqlite3.Error, OSError, ValueError, KeyError):
            return "control_unavailable"
        if not control or control["actor"] != ctx["actor"]:
            return "invalid_context_actor"
        if control["cancel_requested"] and name != "cancel_async_task":
            return "work_cancelled"
        if name in {"start_async_task", "update_async_task"} and control["revision"] != ctx.get(
            "revision"
        ):
            return "stale_revision"
        if name != "cancel_async_task" and time.time() >= deadline:
            return "deadline_exceeded"
        tasks = request.state.get("async_tasks") or {}
        if name == "start_async_task":
            if args.get("subagent_type") not in {"fundamentals", "technicals"}:
                return "agent_type_not_allowed"
            configured_limit = ctx.get("max_children", 2)
            if not isinstance(configured_limit, int) or configured_limit < 0:
                return "invalid_context_child_limit"
            limit = min(2, configured_limit)
            # Parallel tools all see the same pre-tool state. Reserve a slot by
            # their order in the model message; no mutable process-local counter.
            message = next(
                (m for m in reversed(request.state["messages"]) if isinstance(m, AIMessage)), None
            )
            prior_starts = 0
            for call in message.tool_calls if message else []:
                if call["id"] == request.tool_call["id"]:
                    break
                if call["name"] == "start_async_task" and call["args"].get("subagent_type") in {
                    "fundamentals",
                    "technicals",
                }:
                    prior_starts += 1
            if len(tasks) + prior_starts >= limit:
                return "child_limit_exceeded"
        if name in {"update_async_task", "cancel_async_task", "check_async_task"}:
            task = tasks.get(str(args.get("task_id", "")).strip())
            if not task or task.get("agent_name") not in {"fundamentals", "technicals"}:
                return "unregistered_child"
            if name == "update_async_task" and task.get("status") == "waiting":
                return "waiting_for_approval"
        return None

    @staticmethod
    def _blocked(request: ToolCallRequest, reason: str) -> ToolMessage:
        return ToolMessage(
            content=json.dumps({"blocked": reason}),
            name=request.tool_call["name"],
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolResult],
    ) -> ToolResult:
        if reason := self._denial(request):
            return self._blocked(request, reason)
        if request.tool_call["name"] == "update_async_task":
            return self._blocked(request, "async_invocation_required")
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolResult]],
    ) -> ToolResult:
        if reason := await asyncio.to_thread(self._denial, request):
            return self._blocked(request, reason)
        if request.tool_call["name"] == "update_async_task":
            task = request.state["async_tasks"][request.tool_call["args"]["task_id"].strip()]
            try:
                current = await get_client().threads.get(task["thread_id"])
            except Exception:
                return self._blocked(request, "approval_status_unavailable")
            if current.get("status") == "interrupted" or current.get("interrupts"):
                return self._blocked(request, "waiting_for_approval")
        return await handler(request)


def _fixture_tool(role: Literal["fundamentals", "technicals"]) -> BaseTool:
    @tool(f"read_{role}_fixture")
    async def read_fixture(ticker: str, horizon: str, runtime: ToolRuntime[PilotContext]) -> str:
        """Read explicitly synthetic demo scores for a ticker; never queries market data."""
        ctx = runtime.context
        delay = float(ctx.get("delay_seconds", 0.2))
        if not math.isfinite(delay) or delay < 0 or delay > 60:
            raise ValueError("delay_seconds must be between 0 and 60")
        async with asyncio.timeout(max(0, ctx["deadline"] - time.time())):
            await asyncio.sleep(delay)
        values = (
            {"roe": 0.21, "operating_margin": 0.18, "debt_to_equity": 0.4, "revenue_growth": 0.09}
            if role == "fundamentals"
            else {
                "rsi_14": 71,
                "price_vs_sma_200": 1.12,
                "volatility_30d": 0.27,
                "momentum_3m": 0.15,
            }
        )
        return json.dumps(
            {
                "synthetic": True,
                "source": "synthetic_fixture",
                "kind": role,
                "ticker": ticker.upper(),
                "horizon": horizon,
                "revision": ctx["revision"],
                "observations": values,
            }
        )

    return read_fixture


def _supervisor() -> CompiledStateGraph[Any, Any, Any, Any]:
    model = _model("supervisor")
    return make_agent(
        name="async-fin-supervisor",
        model=model,
        context_schema=PilotContext,
        system_prompt=(
            "Coordinate a bounded read-only equity analysis with fundamentals and technicals "
            "children. "
            "All observations are SYNTHETIC FIXTURES, including with a real provider model. "
            "Start at most two children. Return task IDs promptly. Use tracked IDs for updates and "
            "cancellation. Never override a pending human approval. Report synthetic notifications "
            "evidence. Follow new user instructions and preserve their ticker and horizon. "
            "Never recommend a trade: this pilot only gathers synthetic analyst scores."
        ),
        middleware=[
            PilotGuard(
                role="supervisor",
                provenance="offline" if isinstance(model, OfflinePilotModel) else "live",
            ),
            ModelCallLimitMiddleware(run_limit=20, exit_behavior="end"),
            AsyncSubAgentMiddleware(
                async_subagents=[
                    {
                        "name": "fundamentals",
                        "graph_id": "fundamentals",
                        "description": "Read synthetic fundamentals scores.",
                    },
                    {
                        "name": "technicals",
                        "graph_id": "technicals",
                        "description": "Read synthetic technical indicators.",
                    },
                ]
            ),
        ],
    )


def _child(role: Literal["fundamentals", "technicals"]) -> CompiledStateGraph[Any, Any, Any, Any]:
    model = _model(role)
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        PilotGuard(
            role=role, provenance="offline" if isinstance(model, OfflinePilotModel) else "live"
        ),
        ModelCallLimitMiddleware(run_limit=6, exit_behavior="end"),
    ]
    return create_deep_agent(
        model=model,
        tools=[_fixture_tool(role)],
        context_schema=PilotContext,
        name=f"async-fin-{role}",
        system_prompt=(
            f"Read the {role} fixture for the requested ticker and horizon. Return its JSON. "
            "These are SYNTHETIC observations, never production evidence. Use only your fixture "
            "tool. Filesystem, shell, planning and delegation tools are forbidden."
        ),
        middleware=middleware,
    )


async def supervisor(config: RunnableConfig) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build outside the event loop; Agent Server owns checkpointing and execution."""
    return await asyncio.to_thread(_supervisor)


async def fundamentals(config: RunnableConfig) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build the fundamentals child with native Deep Agents parent notification."""
    return await asyncio.to_thread(_child, "fundamentals")


async def technicals(config: RunnableConfig) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build the technicals child with native Deep Agents parent notification."""
    return await asyncio.to_thread(_child, "technicals")
