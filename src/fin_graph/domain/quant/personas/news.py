"""News-only sentiment scorer with explicit empty-data behavior."""

from __future__ import annotations

from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data
from fin_graph.domain.tools import Domain


def score_news(snapshot: CompanySnapshot) -> QuantScore:
    if not snapshot.news:
        return no_data(Domain.SENTIMENT, snapshot.ticker, "company news unavailable")
    positive = sum(item.sentiment == "positive" for item in snapshot.news)
    negative = sum(item.sentiment == "negative" for item in snapshot.news)
    neutral = sum(item.sentiment == "neutral" for item in snapshot.news)
    signal = (
        Signal.BULLISH
        if positive > negative
        else Signal.BEARISH
        if negative > positive
        else Signal.NEUTRAL
    )
    total = positive + negative + neutral
    confidence = round(max(positive, negative) / total * 100, 2) if total else 0.0
    gaps = [] if total else ["news has no sentiment labels"]
    component = Component(
        name="news_sentiment",
        score=float(positive - negative),
        max_score=float(total),
        metrics={
            "total_articles": total,
            "bullish_articles": positive,
            "bearish_articles": negative,
            "neutral_articles": neutral,
            "articles_classified_by_llm": 0,
        },
    )
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
