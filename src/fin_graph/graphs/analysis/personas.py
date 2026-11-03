"""DeepAgents subagent specifications for the nineteen compatibility personas."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from deepagents.middleware.subagents import SubAgent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from fin_graph.agents.middleware.disclaimer import DisclaimerMiddleware
from fin_graph.agents.middleware.evidence_gate import EvidenceGateMiddleware
from fin_graph.domain.analysts import AnalystDefinition
from fin_graph.domain.tools import SideEffectLevel, read_tool_meta
from fin_graph.graphs.analysis.persona_prompts import persona_prompt


@dataclass(frozen=True)
class PersonaToolScope:
    """Request boundary applied to every tool call made by a persona."""

    ticker: str
    as_of: date


PERSONA_TOOL_SCOPE: ContextVar[PersonaToolScope | None] = ContextVar(
    "persona_tool_scope", default=None
)


class _NoFilesystemMiddleware(AgentMiddleware[Any, Any, Any]):
    """Replace DeepAgents' additive filesystem middleware with an empty slot."""

    tools: Sequence[BaseTool] = ()

    @property
    def name(self) -> str:
        return "FilesystemMiddleware"


class _NoSubAgentMiddleware(AgentMiddleware[Any, Any, Any]):
    """Replace the default general-purpose subagent and its ``task`` tool."""

    tools: Sequence[BaseTool] = ()

    @property
    def name(self) -> str:
        return "SubAgentMiddleware"


class PersonaPointInTimeMiddleware(AgentMiddleware[Any, Any, Any]):
    """Force finance-tool ticker/date arguments to the active analysis request."""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        scope = PERSONA_TOOL_SCOPE.get()
        if scope is None or request.tool is None:
            return await handler(request)

        schema = request.tool.get_input_schema()
        fields = cast(Any, schema).model_fields
        args = dict(request.tool_call.get("args", {}))
        if "ticker" in fields:
            args["ticker"] = scope.ticker
        upper_bound = scope.as_of.isoformat()
        for field in ("as_of", "end", "end_date", "date"):
            if field in fields:
                args[field] = upper_bound
        for field in ("start", "start_date"):
            if field not in fields or field not in args:
                continue
            try:
                requested = date.fromisoformat(str(args[field])[:10])
            except ValueError:
                requested = scope.as_of
            if requested > scope.as_of:
                args[field] = upper_bound
        tool_call: Any = {**request.tool_call, "args": args}
        return await handler(request.override(tool_call=tool_call))


def persona_boundary_middleware() -> list[AgentMiddleware[Any, Any, Any]]:
    """Defense-in-depth middleware that leaves only caller-approved tools available."""

    return [
        _NoFilesystemMiddleware(),
        _NoSubAgentMiddleware(),
        PersonaPointInTimeMiddleware(),
    ]


def _read_tools_for(definition: AnalystDefinition, tools: Sequence[BaseTool]) -> list[BaseTool]:
    selected: list[BaseTool] = []
    domains = frozenset(definition.domains)
    for candidate in tools:
        metadata = read_tool_meta(candidate)
        if (
            metadata is not None
            and metadata.side_effect_level is SideEffectLevel.READ_ONLY
            and bool(metadata.domains.intersection(domains))
        ):
            selected.append(candidate)
    return selected


def build_persona_subagents(
    model: BaseChatModel,
    definitions: Sequence[AnalystDefinition],
    tools: Sequence[BaseTool],
) -> list[SubAgent]:
    """Return isolated, read-only DeepAgents specs in the requested order."""

    return [
        cast(
            SubAgent,
            {
                "name": f"analyst_{definition.id.value}",
                "description": (
                    f"{definition.display_name}: {definition.description}. "
                    "Explains the application's deterministic score."
                ),
                "model": model,
                "system_prompt": persona_prompt(definition),
                "tools": _read_tools_for(definition, tools),
                "middleware": [
                    *persona_boundary_middleware(),
                    EvidenceGateMiddleware(),
                    ModelCallLimitMiddleware(run_limit=8),
                    DisclaimerMiddleware(),
                ],
                "mode": "isolated",
            },
        )
        for definition in definitions
    ]
