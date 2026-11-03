"""News-only sentiment scorer with explicit empty-data behavior."""

from __future__ import annotations

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import QuantScore, Signal, no_data
from fin_graph.domain.quant.personas._growth_common import sentiment_component
from fin_graph.domain.tools import Domain


def score_news(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.news:
        return no_data(Domain.SENTIMENT, snapshot.ticker, "company news unavailable")
    component = sentiment_component(snapshot, "news_sentiment")
    positive = int(component.metrics["positive"])
    negative = int(component.metrics["negative"])
    signal = (
        Signal.BULLISH
        if positive > negative
        else Signal.BEARISH
        if negative > positive
        else Signal.NEUTRAL
    )
    labelled = positive + negative
    confidence = abs(positive - negative) / labelled * 100 if labelled else 0.0
    gaps = [] if labelled else ["news has no sentiment labels"]
    return QuantScore(
        analyst=Domain.SENTIMENT,
        ticker=snapshot.ticker,
        signal=signal,
        confidence=confidence,
        score=component.score,
        max_score=component.max_score,
        components=[component],
        data_gaps=gaps,
    )
