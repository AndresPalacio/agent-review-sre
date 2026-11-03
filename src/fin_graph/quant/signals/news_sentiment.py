"""Recent-headline VADER sentiment model."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fin_graph.quant.data import MarketDataClient
from fin_graph.quant.signals.base import QuantModel, Signal


class NewsSentimentModel(QuantModel):
    def __init__(self, *, lookback_days: int = 7, limit: int = 10) -> None:
        from vaderSentiment.vaderSentiment import (  # type: ignore[import-untyped]
            SentimentIntensityAnalyzer,
        )

        self.lookback_days = lookback_days
        self.limit = limit
        self.analyzer: Any = SentimentIntensityAnalyzer()

    @property
    def name(self) -> str:
        return "news_sentiment"

    async def predict(self, ticker: str, as_of: date, data: MarketDataClient) -> Signal:
        articles = await data.company_news(
            ticker,
            as_of,
            start=as_of - timedelta(days=self.lookback_days),
            limit=self.limit,
        )
        scored = [
            (item.title, float(self.analyzer.polarity_scores(item.title)["compound"]))
            for item in articles
            if item.title
        ]
        if not scored:
            return Signal(model_name=self.name, ticker=ticker, as_of=as_of, value=0)
        average = sum(score for _, score in scored) / len(scored)
        headline, _ = max(scored, key=lambda item: abs(item[1]))
        return Signal(
            model_name=self.name,
            ticker=ticker,
            as_of=as_of,
            value=average,
            reasoning=(
                f"Avg VADER compound {average:+.2f} across {len(scored)} headlines "
                f'in the last {self.lookback_days}d (most polarizing: "{headline}")'
            ),
            metadata={"n_articles": len(scored), "avg_compound": average},
        )
