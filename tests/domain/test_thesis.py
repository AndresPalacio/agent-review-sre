from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError
from tests.domain.finance_helpers import evidence_for, finding, request, scores_for

from fin_graph.domain.analysis import (
    AnalysisPlan,
    AnalystTask,
    ThesisHypothesis,
    constrain_analysis_plan,
    degraded_analyst_finding,
    fallback_analysis_plan,
    finding_from_scores,
)
from fin_graph.domain.quant.common import Signal
from fin_graph.domain.requests.model import AnalysisRequest, Horizon
from fin_graph.domain.thesis import (
    InvestmentThesis,
    fallback_thesis,
    suggest_category,
    thesis_schema,
    unknown_thesis,
    vote,
)
from fin_graph.domain.thesis_categories import (
    CATEGORY_NAMES,
    category_text_mismatch,
    normalize_category,
    render_categories,
    stance_mismatch,
)
from fin_graph.domain.tools import ANALYST_DOMAINS, Domain
from fin_graph.domain.window import AnalysisWindow


def test_the_taxonomy_has_the_planned_categories() -> None:
    assert {
        "deep_value", "quality_compounder", "garp", "momentum", "overvalued",
        "deteriorating_fundamentals", "balance_sheet_risk", "negative_sentiment",
        "mixed_signals", "no_edge", "unknown",
    } == CATEGORY_NAMES  # fmt: skip
    assert "- deep_value (bullish):" in render_categories()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Deep Value", "deep_value"),
        ("undervalued", "deep_value"),
        ("hold", "no_edge"),
        ("moon", "unknown"),
    ],
)
def test_normalize_category_fails_closed(raw: str, expected: str) -> None:
    assert normalize_category(raw) == expected


def test_stance_and_text_mismatches() -> None:
    assert stance_mismatch("deep_value", Signal.BEARISH)
    assert stance_mismatch("momentum", Signal.BEARISH) is None
    assert category_text_mismatch("RSI, moving average and a breakout", "deep_value")
    assert category_text_mismatch("Below intrinsic value; the RSI is low", "deep_value") is None
    assert category_text_mismatch("problema de ema", "deep_value") is None


def test_analysis_window_widens_one_three_five_years() -> None:
    window = AnalysisWindow(as_of=date(2024, 3, 8))

    assert window.start == date(2023, 3, 9)
    assert window.contains(date(2024, 3, 8)) and not window.contains(date(2024, 3, 9))
    assert window.expanded().lookback_days == 1095
    assert window.expanded().expanded().expanded().lookback_days == 1825


def test_fallback_plan_follows_the_horizon() -> None:
    long_request = AnalysisRequest(tickers=["AAPL"], as_of=date(2024, 3, 8), horizon=Horizon.LONG)

    plan = fallback_analysis_plan(long_request, set(ANALYST_DOMAINS) - {Domain.GRAHAM}, max_tasks=3)

    assert [task.domain for task in plan.tasks] == [
        Domain.VALUATION,
        Domain.BUFFETT,
        Domain.FUNDAMENTALS,
    ]
    assert [item.id for item in plan.hypotheses] == ["H1", "H2"]


def test_constrain_plan_merges_and_drops_unknowns() -> None:
    hypothesis = ThesisHypothesis(id="H1", statement="s", confirm_if="c", refute_if="r")
    plan = AnalysisPlan(
        hypotheses=[hypothesis],
        tasks=[
            AnalystTask(
                domain=Domain.VALUATION,
                objective="dcf",
                tickers=["AAPL"],
                hypothesis_ids=["H1", "H9"],
            ),
            AnalystTask(domain=Domain.VALUATION, objective="ev/ebitda", tickers=["MSFT"]),
            AnalystTask(domain=Domain.PORTFOLIO, objective="not an analyst"),
        ],
    )

    constrained = constrain_analysis_plan(plan, ANALYST_DOMAINS, {"AAPL", "MSFT"}, max_tasks=5)

    [task] = constrained.tasks
    assert task.objective == "dcf; ev/ebitda"
    assert task.tickers == ["AAPL", "MSFT"] and task.hypothesis_ids == ["H1"]


def test_findings_from_scores_cite_their_evidence() -> None:
    scores = scores_for("deep_value")
    evidence = evidence_for(scores)

    result = finding_from_scores(
        Domain.GRAHAM, [scores[Domain.GRAHAM]], {"DEEP": evidence[Domain.GRAHAM].id}
    )

    assert result.degraded and result.evidence_ids == [evidence[Domain.GRAHAM].id]
    assert result.signal_for("DEEP").signal is Signal.BULLISH  # type: ignore[union-attr]
    assert degraded_analyst_finding(Domain.SENTIMENT, "timeout").confidence == 0


@pytest.mark.parametrize(
    ("name", "category", "stance"),
    [
        ("deep_value", "deep_value", Signal.BULLISH),
        ("distressed", "balance_sheet_risk", Signal.BEARISH),
        ("quality", "overvalued", Signal.BEARISH),
    ],
)
def test_suggest_category_from_scores(name: str, category: str, stance: Signal) -> None:
    assert suggest_category(scores_for(name)) == (category, stance)


def test_suggest_category_without_scores_is_unknown() -> None:
    assert suggest_category({}) == ("unknown", Signal.NEUTRAL)


def test_vote_weights_by_confidence() -> None:
    findings = [
        finding(Domain.VALUATION, "AAPL", Signal.BULLISH, 80),
        finding(Domain.TECHNICALS, "AAPL", Signal.BEARISH, 30),
        finding(Domain.SENTIMENT, "MSFT", Signal.BEARISH, 90),
    ]

    assert vote(findings, "AAPL") == (Signal.BULLISH, 72.7)
    assert vote(findings, "TSLA") == (Signal.NEUTRAL, 0.0)


def test_fallback_thesis_cites_one_score_per_claim() -> None:
    scores = scores_for("deep_value")
    evidence = evidence_for(scores)

    thesis = fallback_thesis("DEEP", scores, {domain: item.id for domain, item in evidence.items()})

    assert thesis.degraded and thesis.category == "deep_value" and thesis.stance is Signal.BULLISH
    assert all(len(claim.evidence_ids) == 1 for claim in thesis.claims)
    assert thesis.bull_case and 0 < thesis.confidence <= 100


def test_fallback_thesis_halves_confidence_when_the_vote_disagrees() -> None:
    scores = scores_for("deep_value")
    ids = {domain: item.id for domain, item in evidence_for(scores).items()}
    against = [finding(Domain.TECHNICALS, "DEEP", Signal.BEARISH, 90)]

    thesis = fallback_thesis("DEEP", scores, ids, against)

    assert thesis.confidence == 50.0


def test_thesis_schema_closes_the_category() -> None:
    schema = thesis_schema(["deep_value", "no_edge"])
    base = {"ticker": "AAPL", "stance": "bullish", "summary": "s", "confidence": 50}

    assert schema(**base, category="deep_value").category == "deep_value"
    with pytest.raises(ValidationError):
        schema(**base, category="overvalued")
    assert unknown_thesis("AAPL", "no data").category == "unknown"
    assert isinstance(schema(**base, category="unknown"), InvestmentThesis)


def test_request_helper_defaults() -> None:
    assert request().tickers == ["AAPL"]
