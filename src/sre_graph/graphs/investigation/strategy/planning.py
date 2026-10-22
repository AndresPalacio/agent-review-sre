"""Editable task-selection policy; the graph applies its fixed constraints afterwards."""

from collections.abc import Sequence

from sre_graph.domain.alerts.model import NormalizedAlert
from sre_graph.domain.investigation import InvestigationPlan
from sre_graph.domain.tools import Domain


def refine_plan(
    plan: InvestigationPlan, alert: NormalizedAlert, available: Sequence[Domain]
) -> InvestigationPlan:
    return plan
