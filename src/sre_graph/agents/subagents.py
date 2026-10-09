"""Subagents pattern: compiled agents that a supervisor calls as tools."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.tools import ToolRuntime
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer
from pydantic import BaseModel, Field

from sre_graph.agents.factory import make_agent


class SubagentTask(BaseModel):
    """The input a supervisor writes for a subagent."""

    task: str = Field(description="A self-contained task with all the context the agent needs.")


@dataclass(frozen=True)
class SubagentSpec:
    """A compiled agent plus the routing text the supervisor's model reads."""

    name: str
    description: str
    agent: CompiledStateGraph[Any, Any, Any, Any]


def subagent_result_text(result: Mapping[str, Any]) -> str:
    """Render a subagent run for its supervisor: structured JSON first, else the reply's text.

    Only the text of the last message is passed on, so provider blocks such as
    thinking or citations never reach the supervisor.
    """
    structured = result.get("structured_response")
    if isinstance(structured, BaseModel):
        return structured.model_dump_json()
    if is_dataclass(structured) and not isinstance(structured, type):
        return json.dumps(asdict(structured), default=str)
    if structured is not None:
        return json.dumps(structured, default=str)
    messages = result.get("messages") or []
    if not messages:
        return "(no response)"
    return str(messages[-1].text)


def subagent_tool(spec: SubagentSpec) -> BaseTool:
    """Expose ``spec.agent`` as a tool; the subagent sees only the task, in its own context.

    The caller's runtime context (where clients and credentials live) is
    forwarded so the subagent's tools can use it.
    """
    agent = spec.agent

    def _run(task: str, runtime: ToolRuntime) -> str:
        result = agent.invoke({"messages": [HumanMessage(task)]}, context=runtime.context)
        return subagent_result_text(result)

    async def _arun(task: str, runtime: ToolRuntime) -> str:
        result = await agent.ainvoke({"messages": [HumanMessage(task)]}, context=runtime.context)
        return subagent_result_text(result)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name=spec.name,
        description=spec.description,
        args_schema=SubagentTask,
    )


def default_supervisor_prompt(subagents: Sequence[SubagentSpec]) -> str:
    """The coordinator prompt used when the caller supplies none."""
    names = ", ".join(spec.name for spec in subagents)
    return (
        "You coordinate a team of specialists. Delegate work to them via your tools "
        f"({names}) and answer the user only once the delegated work is done."
    )


def make_supervisor(
    *,
    subagents: Sequence[SubagentSpec],
    model: BaseChatModel,
    name: str = "supervisor",
    system_prompt: str | None = None,
    tools: Sequence[BaseTool] = (),
    middleware: Sequence[AgentMiddleware[Any, Any]] = (),
    context_schema: type[Any] | None = None,
    checkpointer: Checkpointer = None,
    store: BaseStore | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """A supervisor agent whose tools are its subagents, plus any direct ``tools``."""
    return make_agent(
        name=name,
        model=model,
        tools=[*(subagent_tool(spec) for spec in subagents), *tools],
        system_prompt=system_prompt or default_supervisor_prompt(subagents),
        middleware=middleware,
        context_schema=context_schema,
        checkpointer=checkpointer,
        store=store,
    )
