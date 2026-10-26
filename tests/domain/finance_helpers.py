"""Scores, evidence and theses for the finance domain tests, all from the synthetic companies."""

from __future__ import annotations

from datetime import UTC, date, datetime

from tests.domain.quant.synthetic import (
    COMPANIES,
    bars,
    company_metrics,
    insider_trades,
    line_items,
    news,
)

from fin_graph.domain.analysis import AnalystFinding, TickerSignal
from fin_graph.domain.evidence import EvidenceItem, evidence_id
from fin_graph.domain.quant import buffett, fundamentals, graham, sentiment, technicals, valuation
from fin_graph.domain.quant.common import QuantScore, Signal
from fin_graph.domain.requests.model import AnalysisRequest
from fin_graph.domain.tools import Domain, EvidenceType

AS_OF = date(2024, 3, 8)
COLLECTED = datetime(2024, 3, 8, 21, tzinfo=UTC)
EVIDENCE_TYPES = {
    Domain.FUNDAMENTALS: EvidenceType.METRICS,
    Domain.VALUATION: EvidenceType.VALUATION_MODEL,
    Domain.TECHNICALS: EvidenceType.INDICATORS,
    Domain.SENTIMENT: EvidenceType.NEWS,
    Domain.BUFFETT: EvidenceType.VALUATION_MODEL,
    Domain.GRAHAM: EvidenceType.VALUATION_MODEL,
}


def scores_for(name: str) -> dict[Domain, QuantScore]:
    """Every analyst's score for one synthetic company."""
    ticker, _, market_cap = COMPANIES[name]
    metrics, items = company_metrics(name), line_items(name)
    return {
        Domain.FUNDAMENTALS: fundamentals.score_fundamentals(ticker, metrics),
        Domain.VALUATION: valuation.score_valuation(ticker, metrics[:8], items[:8], market_cap),
        Domain.TECHNICALS: technicals.score_technicals(ticker, bars(drift=-0.004, vol=0.01)),
        Domain.SENTIMENT: sentiment.score_sentiment(
            ticker, insider_trades("TSLA"), news("TSLA", rotate_labels=True)
        ),
        Domain.BUFFETT: buffett.score_buffett(ticker, metrics, items, market_cap),
        Domain.GRAHAM: graham.score_graham(ticker, metrics, items, market_cap),
    }


def evidence_for(scores: dict[Domain, QuantScore]) -> dict[Domain, EvidenceItem]:
    """One evidence item per score, shaped like an analytics tool result."""
    out = {}
    for domain, score in scores.items():
        args = {"ticker": score.ticker, "as_of": AS_OF.isoformat()}
        tool = f"compute_{domain.value}"
        out[domain] = EvidenceItem(
            id=evidence_id(tool, args),
            integration="analytics",
            tool=tool,
            evidence_type=EVIDENCE_TYPES[domain],
            domains=[domain],
            summary=f"{score.ticker} {domain.value} {score.signal.value} {score.confidence:.0f}%",
            args=args,
            data=score.model_dump(mode="json"),
            collected_at=COLLECTED,
        )
    return out


def finding(domain: Domain, ticker: str, signal: Signal, confidence: float) -> AnalystFinding:
    return AnalystFinding(
        domain=domain,
        summary=f"{domain.value} on {ticker}",
        signals=[TickerSignal(ticker=ticker, signal=signal, confidence=confidence)],
        confidence=confidence,
    )


def request(*tickers: str) -> AnalysisRequest:
    return AnalysisRequest(tickers=list(tickers) or ["AAPL"], as_of=AS_OF)
