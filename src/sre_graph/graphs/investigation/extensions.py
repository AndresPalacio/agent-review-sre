"""Fixed validation envelope around editable production harness extensions."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool

from sre_graph.domain.investigation import SpecialistTask
from sre_graph.graphs.investigation.strategy import registration
from sre_graph.graphs.investigation.strategy import tools as tool_policy


def investigation_skill() -> str:
    return (Path(__file__).parent / "strategy" / "skills.md").read_text(encoding="utf-8")


def configured_tools(task: SpecialistTask, tools: Sequence[BaseTool]) -> list[BaseTool]:
    originals = {tool.name: tool for tool in tools}
    # Copies keep an extension from accidentally mutating shared registry tool instances.
    result = tool_policy.prepare_tools(task, [tool.model_copy(deep=True) for tool in tools])
    seen = set()
    for tool in result:
        base = originals.get(tool.name)
        if base is None or tool.name in seen:
            raise ValueError("tool extension returned an unknown or duplicate tool")
        seen.add(tool.name)
        for attr in ("func", "coroutine", "args_schema", "metadata", "response_format"):
            if getattr(tool, attr, None) != getattr(base, attr, None):
                raise ValueError(f"tool extension changed fixed adapter: {tool.name}.{attr}")
    return list(result)


def configured_middleware(task: SpecialistTask) -> list[AgentMiddleware[Any, Any]]:
    result = registration.build_middleware(task)
    if len(result) > 8 or any(not isinstance(item, AgentMiddleware) for item in result):
        raise ValueError("invalid middleware extension")
    fixed = {
        "MaskingMiddleware",
        "GuardrailsMiddleware",
        "ToolCallLimitMiddleware",
        "ModelCallLimitMiddleware",
    }
    names = [item.name for item in result]
    if len(set(names)) != len(names) or fixed.intersection(names):
        raise ValueError("middleware extension collides with fixed safety stack")
    return result
