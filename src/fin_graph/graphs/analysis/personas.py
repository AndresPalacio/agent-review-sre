"""DeepAgents subagent specifications for the nineteen compatibility personas."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from deepagents.middleware.subagents import SubAgent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from fin_graph.agents.middleware.disclaimer import DisclaimerMiddleware
from fin_graph.agents.middleware.evidence_gate import EvidenceGateMiddleware
from fin_graph.domain.analysts import AnalystDefinition
from fin_graph.domain.tools import SideEffectLevel, read_tool_meta
from fin_graph.graphs.analysis.persona_prompts import persona_prompt


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
                    EvidenceGateMiddleware(),
                    ModelCallLimitMiddleware(run_limit=8),
                    DisclaimerMiddleware(),
                ],
                "mode": "isolated",
            },
        )
        for definition in definitions
    ]
