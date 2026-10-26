"""Editable task-selection policy; the graph applies its fixed constraints afterwards."""

from collections.abc import Sequence

from fin_graph.domain.alerts.model import NormalizedAlert
from fin_graph.domain.investigation import InvestigationPlan
from fin_graph.domain.tools import Domain


def refine_plan(
    plan: InvestigationPlan, alert: NormalizedAlert, available: Sequence[Domain]
) -> InvestigationPlan:
    return plan
