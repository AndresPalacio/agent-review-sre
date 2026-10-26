"""The score every analyst returns, and the arithmetic they share.

Confidence is on a 0-100 scale, as in ai-hedge-fund, because analysts and readers quote it
that way ("72% confidence"). A score never invents data: missing inputs become ``data_gaps``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fin_graph.domain.tools import Domain


class Signal(StrEnum):
    """Which way the evidence leans for a ticker."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


_NUMERIC = {Signal.BULLISH: 1, Signal.NEUTRAL: 0, Signal.BEARISH: -1}


class Component(BaseModel):
    """One sub-analysis: its own signal, its points and the numbers behind it."""

    model_config = ConfigDict(frozen=True)

    name: str
    signal: Signal = Signal.NEUTRAL
    score: float | None = None
    max_score: float | None = None
    confidence: float | None = Field(default=None, ge=0, le=100)
    details: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)


class QuantScore(BaseModel):
    """An analyst's deterministic verdict on one ticker."""

    model_config = ConfigDict(frozen=True)

    analyst: Domain
    ticker: str
    signal: Signal
    confidence: float = Field(ge=0, le=100)
    score: float | None = None
    max_score: float | None = None
    components: list[Component] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)

    @property
    def degraded(self) -> bool:
        """True when there was not enough data to score at all."""
        return self.confidence == 0 and self.signal is Signal.NEUTRAL and bool(self.data_gaps)

    def component(self, name: str) -> Component | None:
        """The sub-analysis called ``name``, if it ran."""
        return next((item for item in self.components if item.name == name), None)


def no_data(analyst: Domain, ticker: str, *gaps: str) -> QuantScore:
    """The neutral, zero-confidence score for a ticker with nothing to analyse."""
    return QuantScore(
        analyst=analyst,
        ticker=ticker,
        signal=Signal.NEUTRAL,
        confidence=0,
        data_gaps=list(gaps) or ["no data"],
    )


def majority(signals: Sequence[Signal]) -> tuple[Signal, float]:
    """More bullish than bearish wins; confidence is the winning share of all votes (0-100)."""
    if not signals:
        return Signal.NEUTRAL, 0.0
    bullish = signals.count(Signal.BULLISH)
    bearish = signals.count(Signal.BEARISH)
    if bullish > bearish:
        signal = Signal.BULLISH
    elif bearish > bullish:
        signal = Signal.BEARISH
    else:
        signal = Signal.NEUTRAL
    return signal, round(max(bullish, bearish) / len(signals), 2) * 100


def score_signal(score: float, max_score: float, bullish_at: float = 0.7) -> Signal:
    """Bullish at or above ``bullish_at`` of the maximum, bearish at or below its complement."""
    if max_score <= 0:
        return Signal.NEUTRAL
    if score >= bullish_at * max_score:
        return Signal.BULLISH
    if score <= (1 - bullish_at) * max_score:
        return Signal.BEARISH
    return Signal.NEUTRAL


def weighted_combination(
    parts: Iterable[tuple[Signal, float, float]], threshold: float = 0.2
) -> tuple[Signal, float]:
    """Combine ``(signal, weight, confidence 0-1)`` triples; returns the signal and 0-1 strength.

    Ported from ai-hedge-fund's ``weighted_signal_combination``.
    """
    weighted_sum = 0.0
    total = 0.0
    for signal, weight, confidence in parts:
        weighted_sum += _NUMERIC[signal] * weight * confidence
        total += weight * confidence
    final = weighted_sum / total if total > 0 else 0.0
    if final > threshold:
        return Signal.BULLISH, abs(final)
    if final < -threshold:
        return Signal.BEARISH, abs(final)
    return Signal.NEUTRAL, abs(final)


def finite(value: float | None) -> float | None:
    """``value`` when it is a finite number, else ``None``."""
    if value is None or not math.isfinite(value):
        return None
    return value


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    """``numerator / denominator``, or ``None`` when either is missing or the divisor is zero."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return finite(numerator / denominator)


def pct(value: float | None) -> str:
    """``12.3%`` or ``N/A``."""
    return "N/A" if value is None else f"{value:.1%}"


def num(value: float | None, digits: int = 2) -> str:
    """A number with ``digits`` decimals, or ``N/A``."""
    return "N/A" if value is None else f"{value:,.{digits}f}"
