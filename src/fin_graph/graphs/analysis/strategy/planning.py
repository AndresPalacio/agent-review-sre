"""Editable task-selection policy; the graph applies its fixed constraints afterwards."""

from collections.abc import Sequence

from fin_graph.domain.analysis import AnalysisPlan
from fin_graph.domain.requests.model import AnalysisRequest
from fin_graph.domain.tools import Domain


def refine_plan(
    plan: AnalysisPlan, request: AnalysisRequest, available: Sequence[Domain]
) -> AnalysisPlan:
    return plan
