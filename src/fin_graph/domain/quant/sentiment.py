"""Sentiment: insider buying versus selling (30%) and headline tone (70%).

Ported from ai-hedge-fund ``src/agents/sentiment.py`` and ``news_sentiment.py`` (MIT). There,
headlines without a provider label went to the LLM. Here a finance word list labels them
deterministically, so the score never depends on a model. The analysis graph may still ask the
LLM to relabel and pass the result in through ``labels``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from fin_graph.domain.market import CompanyNews, InsiderTrade, NewsSentiment
from fin_graph.domain.quant.common import Component, QuantScore, Signal, no_data
from fin_graph.domain.tools import Domain

INSIDER_WEIGHT = 0.3
NEWS_WEIGHT = 0.7

_POSITIVE = re.compile(
    r"\b(?:beats?|tops?|record|surg\w*|soar\w*|rall(?:y|ies|ied)|jump\w*|climb\w*|ascend\w*|"
    r"gain(?:s|ed)?|upgrad\w*|outperform\w*|raises? (?:guidance|outlook|forecast|dividend)|"
    r"buybacks?|repurchas\w*|strong (?:demand|growth|results|quarter)|all-time high|"
    r"approv\w*|wins?|partnership|expands?|bullish|profit (?:rises|jumps|grows))\b"
)
_NEGATIVE = re.compile(
    r"\b(?:miss(?:es|ed)?|downgrad\w*|cuts? (?:guidance|outlook|forecast|dividend|jobs)|"
    r"lawsuits?|sued|sues|probe|investigation|recalls?|plung\w*|tumbl\w*|slump\w*|sink\w*|"
    r"falls?|fell|drops?|dropped|declin\w*|loss(?:es)? widen\w*|layoffs?|bankrupt\w*|default\w*|"
    r"fraud|charges?|warns?|warning|weak (?:demand|sales|guidance|results)|sell-?off|bearish|"
    r"delays?|delayed|halt\w*|antitrust|fine[ds]?)\b"
)


def headline_sentiment(title: str) -> NewsSentiment:
    """Label a headline by counting positive and negative finance terms; ties are neutral."""
    text = title.lower()
    positive = len(_POSITIVE.findall(text))
    negative = len(_NEGATIVE.findall(text))
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"


def _lean(bullish: float, bearish: float) -> Signal:
    if bullish > bearish:
        return Signal.BULLISH
    if bearish > bullish:
        return Signal.BEARISH
    return Signal.NEUTRAL


def insider_activity(trades: Sequence[InsiderTrade]) -> Component:
    """Every filing with a share count votes: a sale is bearish, anything else bullish."""
    votes = [
        Signal.BEARISH if trade.transaction_shares < 0 else Signal.BULLISH
        for trade in trades
        if trade.transaction_shares is not None
    ]
    bullish, bearish = votes.count(Signal.BULLISH), votes.count(Signal.BEARISH)
    sold = sum(
        -trade.transaction_shares
        for trade in trades
        if trade.transaction_shares is not None and trade.transaction_shares < 0
    )
    bought = sum(
        trade.transaction_shares
        for trade in trades
        if trade.transaction_shares is not None and trade.transaction_shares > 0
    )
    return Component(
        name="insider_trading",
        signal=_lean(bullish, bearish),
        confidence=round(max(bullish, bearish) / max(len(votes), 1) * 100),
        details=f"{len(votes)} filings: {bullish} buys or grants, {bearish} sales",
        metrics={
            "total_trades": len(votes),
            "bullish_trades": bullish,
            "bearish_trades": bearish,
            "shares_bought": bought,
            "shares_sold": sold,
            "weight": INSIDER_WEIGHT,
        },
    )


def news_tone(
    news: Sequence[CompanyNews], labels: Mapping[str, NewsSentiment] | None = None
) -> Component:
    """Headline votes. ``labels`` (by URL or title) override the provider and the word list."""
    overrides = labels or {}
    votes: list[Signal] = []
    by_lexicon = 0
    for item in news:
        label = overrides.get(item.url) or overrides.get(item.title) or item.sentiment
        if label is None:
            label = headline_sentiment(item.title)
            by_lexicon += 1
        votes.append(
            Signal.BEARISH
            if label == "negative"
            else Signal.BULLISH
            if label == "positive"
            else Signal.NEUTRAL
        )
    bullish, bearish = votes.count(Signal.BULLISH), votes.count(Signal.BEARISH)
    return Component(
        name="news_sentiment",
        signal=_lean(bullish, bearish),
        confidence=round(max(bullish, bearish) / max(len(votes), 1) * 100),
        details=f"{len(votes)} headlines: {bullish} positive, {bearish} negative",
        metrics={
            "total_articles": len(votes),
            "bullish_articles": bullish,
            "bearish_articles": bearish,
            "neutral_articles": votes.count(Signal.NEUTRAL),
            "labelled_by_lexicon": by_lexicon,
            "weight": NEWS_WEIGHT,
        },
    )


def score_sentiment(
    ticker: str,
    trades: Sequence[InsiderTrade],
    news: Sequence[CompanyNews],
    labels: Mapping[str, NewsSentiment] | None = None,
) -> QuantScore:
    """Weighted vote counts: insiders 0.3 per filing, headlines 0.7 per article."""
    if not trades and not news:
        return no_data(Domain.SENTIMENT, ticker, "no insider trades and no news")
    insiders, headlines = insider_activity(trades), news_tone(news, labels)
    insider_votes = int(insiders.metrics["total_trades"])
    news_votes = int(headlines.metrics["total_articles"])
    bullish = (
        insiders.metrics["bullish_trades"] * INSIDER_WEIGHT
        + headlines.metrics["bullish_articles"] * NEWS_WEIGHT
    )
    bearish = (
        insiders.metrics["bearish_trades"] * INSIDER_WEIGHT
        + headlines.metrics["bearish_articles"] * NEWS_WEIGHT
    )
    total = insider_votes * INSIDER_WEIGHT + news_votes * NEWS_WEIGHT
    gaps = []
    if not trades:
        gaps.append("no insider trades")
    if not news:
        gaps.append("no news")
    return QuantScore(
        analyst=Domain.SENTIMENT,
        ticker=ticker,
        signal=_lean(bullish, bearish),
        confidence=round(max(bullish, bearish) / total * 100, 2) if total > 0 else 0,
        components=[insiders, headlines],
        data_gaps=gaps,
    )
