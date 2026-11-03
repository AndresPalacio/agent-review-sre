"""Editable tool selection, ordering and descriptions; adapters remain fixed."""

from collections.abc import Sequence

from langchain_core.tools import BaseTool

from fin_graph.domain.analysis import AnalystTask


def prepare_tools(task: AnalystTask, tools: Sequence[BaseTool]) -> list[BaseTool]:
    """Return original tools or model_copy(description=...) variants, without mutating inputs."""
    return list(tools)
