from __future__ import annotations

from tests.domain.finance_helpers import AS_OF, evidence_for, request, scores_for
from tests.domain.quant.synthetic import bars

from fin_graph.domain.analysis import finding_from_scores
from fin_graph.domain.analysis_report import DISCLAIMER, build_analysis_report
from fin_graph.domain.orders import OrderAction, PortfolioDecision, propose_order
from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.quant.risk import position_limits
from fin_graph.domain.rendering.analysis import (
    MESSAGE_LIMIT,
    render_analysis_markdown,
    render_analysis_slack,
    render_analysis_telegram,
)
from fin_graph.domain.thesis import ThesisClaim, fallback_thesis
from fin_graph.domain.thesis_verification import verify_thesis


def _report(**overrides: object):  # type: ignore[no-untyped-def]
    scores = scores_for("deep_value")
    evidence = evidence_for(scores)
    ids = {domain: item.id for domain, item in evidence.items()}
    thesis = fallback_thesis("DEEP", scores, ids)
    findings = [
        finding_from_scores(domain, [score], {"DEEP": ids[domain]})
        for domain, score in scores.items()
    ]
    limits = position_limits(["DEEP"], {"DEEP": bars(n=120, seed=3)}, Portfolio())
    decision = PortfolioDecision(
        action=OrderAction.BUY, quantity=12, confidence=60, reasoning="net-net"
    )
    order = propose_order(
        decision, ticker="DEEP", price=limits["DEEP"].current_price, as_of=AS_OF, run_id="run-1"
    )
    kwargs: dict[str, object] = {
        "findings": findings,
        "verifications": {"DEEP": verify_thesis(thesis, evidence.values())},
        "limits": limits,
        "decisions": {"DEEP": decision},
        "orders": [order] if order else [],
        "degraded_steps": ["synthesize: fallback thesis", "synthesize: fallback thesis"],
        "duration_seconds": 42.0,
    }
    kwargs.update(overrides)
    return build_analysis_report(request("DEEP", "MISS"), [thesis], evidence.values(), **kwargs)  # type: ignore[arg-type]


def test_the_report_labels_cited_evidence_in_citation_order() -> None:
    report = _report()

    [section] = report.sections
    assert section.ticker == "DEEP"
    assert list(report.labels.values()) == [f"E{n}" for n in range(1, len(report.labels) + 1)]
    assert all(claim.citations for claim in section.claims)
    assert set(section.signals) == {
        "fundamentals",
        "valuation",
        "technicals",
        "sentiment",
        "buffett",
        "graham",
    }
    assert report.degraded_steps == ["synthesize: fallback thesis"]
    assert report.orders and report.orders[0].ticker == "DEEP"


def test_markdown_has_every_section_and_the_disclaimer() -> None:
    text = render_analysis_markdown(_report())

    for heading in (
        "# Análisis DEEP, MISS al 2024-03-08",
        "## DEEP — alcista · deep_value",
        "**Señales**",
        "**A favor**",
        "**Afirmaciones**",
        "Riesgo: precio",
        "Decisión: comprar 12 acciones",
        "pendiente de aprobación",
        "## Evidencia",
        "## Pasos degradados",
    ):
        assert heading in text, heading
    assert DISCLAIMER in text
    assert "Generado por fin-graph en 42s" in text


def test_unknown_citations_are_flagged_not_labelled() -> None:
    scores = scores_for("deep_value")
    evidence = evidence_for(scores)
    thesis = fallback_thesis("DEEP", scores, {}).model_copy(
        update={"claims": [ThesisClaim(statement="P/E is 8.", evidence_ids=["ev_missing"])]}
    )

    report = build_analysis_report(
        request("DEEP"),
        [thesis],
        evidence.values(),
        verifications={"DEEP": verify_thesis(thesis, evidence.values())},
    )

    assert report.labels == {} and report.evidence == []
    assert "sin verificar" in render_analysis_markdown(report)


def test_slack_and_telegram_stay_within_their_limits() -> None:
    report = _report()
    section = report.sections[0]
    noisy = section.thesis.model_copy(update={"summary": "<x> " * 3000})
    long = report.model_copy(
        update={"sections": [section.model_copy(update={"thesis": noisy})] * 3}
    )

    slack = render_analysis_slack(report)
    telegram = render_analysis_telegram(long)

    assert slack["text"].startswith("Análisis DEEP") and slack["blocks"][0]["type"] == "header"
    assert all(len(block.get("text", {}).get("text", "")) <= 3000 for block in slack["blocks"])
    assert len(telegram) <= MESSAGE_LIMIT
    assert "&lt;x&gt;" in telegram and "(recortado)" in telegram
    assert telegram.endswith(f"<i>{DISCLAIMER}</i>")
