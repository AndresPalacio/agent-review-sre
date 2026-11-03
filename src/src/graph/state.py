"""Legacy graph-state shape; execution is handled by the native graph."""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage


def merge_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {**a, **b}


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]
    data: Annotated[dict[str, Any], merge_dicts]
    metadata: Annotated[dict[str, Any], merge_dicts]


def show_agent_reasoning(output: object, agent_name: str) -> None:
    print(f"{agent_name}: {json.dumps(output, ensure_ascii=False, indent=2, default=str)}")


__all__ = ["AgentState", "merge_dicts", "show_agent_reasoning"]
