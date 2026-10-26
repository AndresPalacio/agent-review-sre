"""The investment thesis per ticker: stance, both cases, and claims that cite evidence."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, create_model

from fin_graph.domain.analysis import AnalystFinding, Verdict
from fin_graph.domain.quant.common import QuantScore, Signal
from fin_graph.domain.thesis_categories import (
    CATEGORY_NAMES,
    MIXED_SIGNALS,
    NO_EDGE,
    UNKNOWN,
    render_categories,
)
from fin_graph.domain.tools import Domain

#: A vote counts as conviction at or above this confidence (0-100).
CONVICTION = 50


class HypothesisOutcome(BaseModel):
    """How one hypothesis ended up for this ticker."""

    hypothesis_id: str
    statement: str
    verdict: Verdict
    reason: str


class ThesisClaim(BaseModel):
    """A factual statement in the thesis and the evidence ids that back it.

    ``source`` is set by the system, never taken from a model: ``scores`` claims restate a quant
    score and need no judge; ``model`` claims are judged.
    """

    statement: str
    evidence_ids: list[str] = Field(
        default_factory=list, description="Ids of the evidence items that support it."
    )
    source: Literal["scores", "model"] = "model"


class InvestmentThesis(BaseModel):
    """The synthesizer's conclusion for one ticker. ``category`` must name a thesis category."""

    ticker: str
    stance: Signal
    summary: str = Field(description="The thesis in two or three sentences.")
    category: str
    confidence: float = Field(ge=0, le=100, description="0-100.")
    bull_case: list[str] = Field(default_factory=list, description="Reasons it could work.")
    bear_case: list[str] = Field(default_factory=list, description="Reasons it could fail.")
    catalysts: list[str] = Field(default_factory=list, description="Events that could move it.")
    risks: list[str] = Field(default_factory=list, description="What would invalidate it.")
    hypotheses: list[HypothesisOutcome] = Field(default_factory=list)
    claims: list[ThesisClaim] = Field(default_factory=list)
    degraded: bool = Field(
        default=False, description="True when a deterministic fallback wrote it."
    )


def thesis_schema(categories: Collection[str] | None = None) -> type[InvestmentThesis]:
    """``InvestmentThesis`` with ``category`` as a closed enum, for structured model output."""
    allowed = (CATEGORY_NAMES if categories is None else frozenset(categories) & CATEGORY_NAMES) | {
        UNKNOWN
    }
    names = tuple(sorted(allowed))
    category_type = cast(Any, Literal.__getitem__(names))
    return create_model(
        "InvestmentThesis",
        __base__=InvestmentThesis,
        category=(
            category_type,
            Field(
                description="Exactly one category from this taxonomy:\n" + render_categories(names)
            ),
        ),
    )


def unknown_thesis(ticker: str, reason: str) -> InvestmentThesis:
    """The thesis to report when the analysis could not reach a conclusion."""
    return InvestmentThesis(
        ticker=ticker,
        stance=Signal.NEUTRAL,
        summary=f"Sin tesis para {ticker}: {reason}",
        category=UNKNOWN,
        confidence=0,
        risks=["El análisis está incompleto; no actúes sobre él."],
        degraded=True,
    )


def _score(scores: Mapping[Domain, QuantScore], domain: Domain) -> QuantScore | None:
    score = scores.get(domain)
    return None if score is None or score.degraded else score


def _component_signal(score: QuantScore | None, name: str) -> Signal | None:
    part = score.component(name) if score else None
    return part.signal if part else None


#: Liabilities over assets, and a current ratio, past which a weak balance sheet is distress.
DISTRESS_DEBT_RATIO = 0.9
DISTRESS_CURRENT_RATIO = 0.8


def _distressed(graham: QuantScore | None, fundamentals: QuantScore | None) -> bool:
    """Graham strength at most 1 of 5, plus bearish health or extreme leverage or illiquidity."""
    strength = graham.component("financial_strength") if graham else None
    if strength is None or (strength.score or 0) > 1:
        return False
    debt, current = strength.metrics.get("debt_ratio"), strength.metrics.get("current_ratio")
    return (
        _component_signal(fundamentals, "financial_health") is Signal.BEARISH
        or (debt is not None and debt >= DISTRESS_DEBT_RATIO)
        or (current is not None and current < DISTRESS_CURRENT_RATIO)
    )


def suggest_category(scores: Mapping[Domain, QuantScore]) -> tuple[str, Signal]:
    """The category and stance the quant scores alone support, checked from strongest to weakest.

    Used as the synthesizer's fallback and to sanity-check the model: balance-sheet distress
    first, then value gaps, quality, growth, price action and sentiment. Strong disagreement
    is ``mixed_signals``, nothing strong is ``no_edge``.
    """
    usable = {domain: score for domain, score in scores.items() if not score.degraded}
    if not usable:
        return UNKNOWN, Signal.NEUTRAL
    valuation, graham = _score(usable, Domain.VALUATION), _score(usable, Domain.GRAHAM)
    buffett, fundamentals = _score(usable, Domain.BUFFETT), _score(usable, Domain.FUNDAMENTALS)
    technicals, sentiment = _score(usable, Domain.TECHNICALS), _score(usable, Domain.SENTIMENT)
    if _distressed(graham, fundamentals):
        return "balance_sheet_risk", Signal.BEARISH
    cheap = [s for s in (valuation, graham) if s and s.signal is Signal.BULLISH]
    dear = [s for s in (valuation, graham) if s and s.signal is Signal.BEARISH]
    if cheap and not dear and max(s.confidence for s in cheap) >= CONVICTION:
        return "deep_value", Signal.BULLISH
    if buffett and buffett.signal is Signal.BULLISH:
        return "quality_compounder", Signal.BULLISH
    if (
        _component_signal(fundamentals, "growth") is Signal.BULLISH
        and not dear
        and _component_signal(fundamentals, "price_ratios") is not Signal.BEARISH
    ):
        return "garp", Signal.BULLISH
    if dear and not cheap and max(s.confidence for s in dear) >= CONVICTION:
        return "overvalued", Signal.BEARISH
    if (
        _component_signal(fundamentals, "growth") is Signal.BEARISH
        and _component_signal(fundamentals, "profitability") is Signal.BEARISH
    ):
        return "deteriorating_fundamentals", Signal.BEARISH
    if (
        technicals
        and technicals.signal is not Signal.NEUTRAL
        and technicals.confidence >= CONVICTION
    ):
        return "momentum", technicals.signal
    if sentiment and sentiment.signal is Signal.BEARISH and sentiment.confidence >= CONVICTION:
        return "negative_sentiment", Signal.BEARISH
    convinced = [s.signal for s in usable.values() if s.confidence >= CONVICTION]
    if Signal.BULLISH in convinced and Signal.BEARISH in convinced:
        return MIXED_SIGNALS, Signal.NEUTRAL
    return NO_EDGE, Signal.NEUTRAL


def vote(findings: Sequence[AnalystFinding], ticker: str) -> tuple[Signal, float]:
    """Confidence-weighted vote of the analysts' signals for ``ticker`` (0-100)."""
    weights = {Signal.BULLISH: 0.0, Signal.BEARISH: 0.0, Signal.NEUTRAL: 0.0}
    for finding in findings:
        signal = finding.signal_for(ticker)
        if signal is not None:
            weights[signal.signal] += signal.confidence
    total = sum(weights.values())
    if total == 0:
        return Signal.NEUTRAL, 0.0
    if weights[Signal.BULLISH] > weights[Signal.BEARISH]:
        winner = Signal.BULLISH
    elif weights[Signal.BEARISH] > weights[Signal.BULLISH]:
        winner = Signal.BEARISH
    else:
        winner = Signal.NEUTRAL
    return winner, round(weights[winner] / total * 100, 1)


def fallback_thesis(
    ticker: str,
    scores: Mapping[Domain, QuantScore],
    evidence_ids: Mapping[Domain, str],
    findings: Sequence[AnalystFinding] = (),
) -> InvestmentThesis:
    """A thesis written from the scores alone, every claim citing the score it states.

    The stance comes from :func:`suggest_category`, the confidence from the analysts' vote when
    it agrees with that stance, and is halved when it does not.
    """
    category, stance = suggest_category(scores)
    if category == UNKNOWN:
        return unknown_thesis(ticker, "no analyst produced a usable score")
    voted, voted_confidence = vote(findings, ticker) if findings else (stance, 0.0)
    if not findings:
        confidences = [score.confidence for score in scores.values() if score.signal is stance]
        voted_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    confidence = voted_confidence if voted is stance else voted_confidence / 2
    claims, bull, bear = [], [], []
    for domain, score in scores.items():
        if score.degraded:
            continue
        line = (
            f"{domain.value}: {_STANCE_ES[score.signal]} con {score.confidence:.0f}% de confianza"
        )
        claims.append(
            ThesisClaim(
                statement=f"El analista {line}.",
                evidence_ids=[evidence_ids[domain]] if domain in evidence_ids else [],
                source="scores",
            )
        )
        if score.signal is Signal.BULLISH:
            bull.append(line)
        elif score.signal is Signal.BEARISH:
            bear.append(line)
    neutral = [
        f"{domain.value}: neutral con {score.confidence:.0f}% de confianza"
        for domain, score in scores.items()
        if not score.degraded and score.signal is Signal.NEUTRAL
    ]
    bullish_votes, bearish_votes = len(bull), len(bear)
    # Both cases are required: a one-sided vote still has a weak side, and saying so is the case.
    bull = bull or neutral or ["Ningún analista es alcista."]
    bear = (
        bear
        or neutral
        or ["Ningún analista es bajista; el riesgo es que los scores miran datos pasados."]
    )
    gaps = [f"{domain.value}: {gap}" for domain, score in scores.items() for gap in score.data_gaps]
    return InvestmentThesis(
        ticker=ticker,
        stance=stance,
        summary=f"{ticker}: {category} según los scores cuantitativos "
        f"(analistas alcistas: {bullish_votes}, bajistas: {bearish_votes}).",
        category=category,
        confidence=round(confidence, 1),
        bull_case=bull,
        bear_case=bear,
        risks=gaps[:5] or ["Solo scores: ningún modelo revisó la evidencia."],
        claims=claims,
        degraded=True,
    )


#: The deterministic thesis is user-facing, so it is written in Spanish like the report.
_STANCE_ES = {Signal.BULLISH: "alcista", Signal.BEARISH: "bajista", Signal.NEUTRAL: "neutral"}
