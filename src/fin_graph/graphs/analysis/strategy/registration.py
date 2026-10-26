"""Editable wiring for optional analyst middleware."""

from typing import Any

from langchain.agents.middleware import AgentMiddleware

from fin_graph.domain.analysis import AnalystTask
from fin_graph.graphs.analysis.strategy.middleware import AnalysisDiscipline


def build_middleware(task: AnalystTask) -> list[AgentMiddleware[Any, Any]]:
    return [AnalysisDiscipline()]
