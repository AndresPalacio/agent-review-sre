from __future__ import annotations

from tests.domain.finance_helpers import evidence_for, scores_for

from fin_graph.domain.claims import ClaimStatus
from fin_graph.domain.quant.common import Signal
from fin_graph.domain.thesis import InvestmentThesis, ThesisClaim, fallback_thesis
from fin_graph.domain.thesis_verification import check_claim, missing_sections, verify_thesis
from fin_graph.domain.tools import Domain


def _thesis(**overrides: object) -> InvestmentThesis:
    base: dict[str, object] = {
        "ticker": "DEEP",
        "stance": Signal.BULLISH,
        "summary": "Trades well below intrinsic value with a solid balance sheet.",
        "category": "deep_value",
        "confidence": 80,
        "bull_case": ["net-net discount"],
        "bear_case": ["low growth"],
        "risks": ["value trap"],
    }
    return InvestmentThesis.model_validate({**base, **overrides})


def test_a_claim_must_cite_the_kind_of_evidence_it_talks_about() -> None:
    evidence = evidence_for(scores_for("deep_value"))
    by_id = {item.id: item for item in evidence.values()}
    graham_id = evidence[Domain.GRAHAM].id
    fundamentals_id = evidence[Domain.FUNDAMENTALS].id

    supported = check_claim(
        ThesisClaim(statement="The Graham Number is above the price.", evidence_ids=[graham_id]),
        by_id,
    )
    weak = check_claim(
        ThesisClaim(statement="The RSI is oversold.", evidence_ids=[fundamentals_id]), by_id
    )
    missing = check_claim(ThesisClaim(statement="P/E is 8.", evidence_ids=["ev_nope"]), by_id)
    uncited = check_claim(ThesisClaim(statement="Cheap."), by_id)

    assert supported.status is ClaimStatus.SUPPORTED
    assert weak.status is ClaimStatus.WEAK and "indicators" in weak.reason
    assert missing.status is ClaimStatus.UNSUPPORTED
    assert uncited.status is ClaimStatus.UNSUPPORTED


def test_the_deterministic_thesis_passes_its_own_verification() -> None:
    scores = scores_for("deep_value")
    evidence = evidence_for(scores)
    thesis = fallback_thesis("DEEP", scores, {domain: item.id for domain, item in evidence.items()})

    report = verify_thesis(thesis, evidence.values())

    assert report.missing_sections == [] and report.stance_conflict is None
    assert all(check.status is ClaimStatus.SUPPORTED for check in report.claims)
    assert report.score == round(thesis.confidence / 100, 2)


def test_bullish_against_negative_valuation_gap_is_a_hard_conflict() -> None:
    scores = scores_for("quality")  # valuation puts intrinsic value far below the price
    evidence = evidence_for(scores)
    valuation_id = evidence[Domain.VALUATION].id
    thesis = _thesis(
        ticker="AAPL",
        category="quality_compounder",
        summary="High returns on capital and durable margins.",
        claims=[ThesisClaim(statement="The DCF supports the price.", evidence_ids=[valuation_id])],
    )

    report = verify_thesis(thesis, evidence.values())

    assert report.stance_conflict and valuation_id in report.stance_conflict
    assert not report.passed


def test_category_stance_and_summary_must_agree() -> None:
    contradicted = verify_thesis(_thesis(stance=Signal.BEARISH), [])
    off_topic = verify_thesis(
        _thesis(summary="RSI, moving average and a breakout above the EMA."), []
    )

    assert contradicted.stance_mismatch and "bullish" in contradicted.stance_mismatch
    assert not contradicted.passed
    assert off_topic.category_mismatch and "price_action" in off_topic.category_mismatch


def test_a_concluded_thesis_needs_both_cases_and_claims() -> None:
    assert missing_sections(_thesis(bull_case=[], claims=[])) == ["bull_case", "claims"]
    assert (
        missing_sections(
            _thesis(category="no_edge", stance=Signal.NEUTRAL, bull_case=[], bear_case=[])
        )
        == []
    )


def test_no_edge_without_claims_keeps_its_confidence() -> None:
    report = verify_thesis(
        _thesis(category="no_edge", stance=Signal.NEUTRAL, summary="Fairly priced.", confidence=60),
        [],
    )

    assert report.score == 0.6 and report.passed


def test_a_sound_low_confidence_thesis_passes_with_a_low_score() -> None:
    report = verify_thesis(
        _thesis(category="no_edge", stance=Signal.NEUTRAL, summary="Fairly priced.", confidence=20),
        [],
    )

    assert report.passed and report.score == 0.2


def test_model_claims_are_grounded_in_the_cited_figures() -> None:
    from fin_graph.domain.thesis_verification import ground_numbers, numeric_check

    fundamentals = evidence_for(scores_for("quality"))[Domain.FUNDAMENTALS]  # AAPL fixture
    by_id = {fundamentals.id: fundamentals}

    assert ground_numbers("El ROE es del 156.0% y el P/E de 29.67.", [fundamentals]) == (
        ["156.0%", "29.67"],
        [],
    )
    assert ground_numbers("Ganará 50% en 2025.", [fundamentals]) == ([], ["50%"])
    assert ground_numbers("Tiene 3 negocios.", [fundamentals]) == ([], [])

    good = numeric_check(
        ThesisClaim(statement="D/E de 3.77", evidence_ids=[fundamentals.id]), by_id
    )
    bad = numeric_check(ThesisClaim(statement="ROE de 12%", evidence_ids=[fundamentals.id]), by_id)
    none = numeric_check(ThesisClaim(statement="Es caro.", evidence_ids=[fundamentals.id]), by_id)

    assert good is not None and good.status is ClaimStatus.SUPPORTED
    assert bad is not None and bad.status is ClaimStatus.UNSUPPORTED and "12%" in bad.reason
    assert none is None
