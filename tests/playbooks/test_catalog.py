from __future__ import annotations

import pytest
from tests.domain.finance_helpers import scores_for

from fin_graph.playbooks.catalog import PLAYBOOKS, load_playbook, select_playbooks
from fin_graph.skills.catalog import SKILLS, load_skill


def test_the_seven_planned_playbooks_exist() -> None:
    assert set(PLAYBOOKS) == {
        "earnings-season", "high-volatility-regime", "deep-value-checklist",
        "quality-compounder-checklist", "balance-sheet-stress", "insider-selling-review",
        "momentum-breakdown",
    }  # fmt: skip
    with pytest.raises(KeyError):
        load_playbook("buy-everything")


def test_playbooks_follow_the_category_and_scores() -> None:
    deep = [item["id"] for item in select_playbooks("deep_value", scores_for("deep_value"))]
    stress = [
        item["id"] for item in select_playbooks("balance_sheet_risk", scores_for("distressed"))
    ]
    earnings = select_playbooks(
        "no_edge", {}, question="¿Cómo vienen los resultados del trimestre?"
    )

    assert deep[0] == "deep-value-checklist" and len(deep) <= 2
    assert stress[0] == "balance-sheet-stress"
    assert earnings[0]["id"] == "earnings-season"


def test_finance_skills_are_curated_in_spanish() -> None:
    expected = {
        "analyzing-equities", "fundamental-filter", "financial-statement", "risk-analysis",
        "sentiment-analysis", "asset-allocation", "behavioral-finance",
    }  # fmt: skip
    assert expected <= set(SKILLS)
    assert "aprobación humana" in load_skill("analyzing-equities", full=True)
