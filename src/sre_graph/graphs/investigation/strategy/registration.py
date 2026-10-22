"""Editable wiring for optional investigation middleware."""

from typing import Any

from langchain.agents.middleware import AgentMiddleware

from sre_graph.domain.investigation import SpecialistTask
from sre_graph.graphs.investigation.strategy.middleware import InvestigationDiscipline


def build_middleware(task: SpecialistTask) -> list[AgentMiddleware[Any, Any]]:
    return [InvestigationDiscipline()]
