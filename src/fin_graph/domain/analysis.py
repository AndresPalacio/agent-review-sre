"""The analysis plan (falsifiable theses and analyst tasks) and what each analyst reports back.

Same shape as the incident investigation: the planner states hypotheses with what would confirm
or refute them, each analyst domain gets one task, and every finding cites evidence ids. A
finding adds one signal per ticker, so the portfolio step can read the vote directly.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from enum import StrEnum

from pydantic import BaseModel, Field

from fin_graph.domain.quant.common import QuantScore, Signal
from fin_graph.domain.requests.model import AnalysisRequest, Horizon
from fin_graph.domain.tools import ANALYST_DOMAINS, Domain

MAX_HYPOTHESES = 4

#: Analysts worth asking first for each horizon, in priority order.
HORIZON_ANALYSTS: dict[Horizon, tuple[Domain, ...]] = {
    Horizon.SHORT: (Domain.TECHNICALS, Domain.SENTIMENT, Domain.FUNDAMENTALS, Domain.VALUATION),
    Horizon.MEDIUM: ANALYST_DOMAINS,
    Horizon.LONG: (
        Domain.VALUATION,
        Domain.BUFFETT,
        Domain.GRAHAM,
        Domain.FUNDAMENTALS,
        Domain.SENTIMENT,
        Domain.TECHNICALS,
    ),
}


class ThesisHypothesis(BaseModel):
    """A falsifiable investment idea and how to tell whether it holds."""

    id: str = Field(description='Short id the tasks and verdicts refer to, for example "H1".')
    statement: str = Field(description="The idea, in one sentence, naming the ticker.")
    confirm_if: str = Field(description="What evidence would confirm it.")
    refute_if: str = Field(description="What evidence would refute it.")


class AnalystTask(BaseModel):
    """What one analyst should check, and for which tickers (empty: all of them)."""

    domain: Domain
    objective: str = Field(description="What to check, concretely, with this analyst's tools.")
    tickers: list[str] = Field(default_factory=list, description="Tickers to cover; empty = all.")
    hypothesis_ids: list[str] = Field(
        default_factory=list, description="Hypotheses this task tests."
    )


class AnalysisPlan(BaseModel):
    """The planner's output: 1 to 4 hypotheses and the analyst tasks that test them."""

    hypotheses: list[ThesisHypothesis]
    tasks: list[AnalystTask]


class Verdict(StrEnum):
    """Where the evidence leaves a hypothesis."""

    SUPPORTS = "supports"
    REFUTES = "refutes"
    INCONCLUSIVE = "inconclusive"


class HypothesisVerdict(BaseModel):
    """An analyst's verdict on one hypothesis, with the evidence behind it."""

    hypothesis_id: str
    verdict: Verdict
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class TickerSignal(BaseModel):
    """One analyst's vote on one ticker."""

    ticker: str
    signal: Signal
    confidence: float = Field(ge=0, le=100, description="0-100.")
    rationale: str = Field(default="", description="One sentence citing the numbers.")


class AnalystFinding(BaseModel):
    """What an analyst found, citing the evidence ids its tool calls produced."""

    domain: Domain
    summary: str = Field(description="What the evidence shows, in two or three sentences.")
    signals: list[TickerSignal] = Field(default_factory=list)
    verdicts: list[HypothesisVerdict] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=100, description="0 = no idea, 100 = certain.")
    gaps: list[str] = Field(default_factory=list, description="What could not be checked, and why.")
    degraded: bool = Field(
        default=False, description="True when a deterministic fallback wrote it."
    )

    def signal_for(self, ticker: str) -> TickerSignal | None:
        return next((item for item in self.signals if item.ticker == ticker), None)


def degraded_analyst_finding(domain: Domain, error: str) -> AnalystFinding:
    """The finding a failed analyst contributes, so the analysis can carry on."""
    return AnalystFinding(
        domain=domain,
        summary=f"The {domain.value} analyst could not finish: {error}",
        confidence=0,
        gaps=[f"{domain.value} evidence is missing because the analyst failed"],
        degraded=True,
    )


def finding_from_scores(
    domain: Domain,
    scores: Sequence[QuantScore],
    evidence_ids: Mapping[str, str],
    *,
    note: str = "",
) -> AnalystFinding:
    """The deterministic finding: each ticker's quant score becomes its signal and its citation.

    ``evidence_ids`` maps ticker → the evidence id of the score's tool call. Used when the
    model fails, and whenever the model's signal contradicts the score it was given.
    """
    signals = []
    parts = []
    for score in scores:
        reasons = "; ".join(item.details for item in score.components if item.details)[:220]
        if not reasons:
            # Technicals carry numbers, not prose: name each strategy's vote instead.
            reasons = ", ".join(
                f"{item.name} {item.signal.value}"
                + (f" {item.confidence:.0f}%" if item.confidence is not None else "")
                for item in score.components
            )
        signals.append(
            TickerSignal(
                ticker=score.ticker,
                signal=score.signal,
                confidence=score.confidence,
                rationale=reasons or f"{domain.value} score",
            )
        )
        parts.append(f"{score.ticker}: {score.signal.value} ({score.confidence:.0f}%)")
    gaps = [f"{score.ticker}: {gap}" for score in scores for gap in score.data_gaps]
    confidences = [score.confidence for score in scores]
    summary = f"{domain.value} scores: " + ", ".join(parts) if parts else f"{domain.value}: no data"
    return AnalystFinding(
        domain=domain,
        summary=f"{summary}. {note}".strip() if note else summary,
        signals=signals,
        evidence_ids=[
            evidence_ids[score.ticker] for score in scores if score.ticker in evidence_ids
        ],
        confidence=sum(confidences) / len(confidences) if confidences else 0,
        gaps=gaps,
        degraded=True,
    )


def constrain_analysis_plan(
    plan: AnalysisPlan, available: Collection[Domain], tickers: Collection[str], max_tasks: int
) -> AnalysisPlan:
    """Keep only what can run: available analysts, one task each, known tickers, ``max_tasks``."""
    hypotheses = plan.hypotheses[:MAX_HYPOTHESES]
    known = {hypothesis.id for hypothesis in hypotheses}
    merged: dict[Domain, AnalystTask] = {}
    for task in plan.tasks:
        if task.domain not in available:
            continue
        ids = [item for item in task.hypothesis_ids if item in known]
        wanted = [ticker for ticker in task.tickers if ticker in tickers]
        previous = merged.get(task.domain)
        if previous is None:
            merged[task.domain] = task.model_copy(
                update={"hypothesis_ids": _unique(ids), "tickers": _unique(wanted)}
            )
        else:
            merged[task.domain] = previous.model_copy(
                update={
                    "objective": f"{previous.objective}; {task.objective}",
                    "hypothesis_ids": _unique([*previous.hypothesis_ids, *ids]),
                    "tickers": _unique([*previous.tickers, *wanted])
                    if previous.tickers and wanted
                    else [],
                }
            )
    return AnalysisPlan(hypotheses=hypotheses, tasks=list(merged.values())[:max_tasks])


def fallback_analysis_plan(
    request: AnalysisRequest, available: Collection[Domain], max_tasks: int
) -> AnalysisPlan:
    """A deterministic plan for when the planner model fails.

    Two generic theses per request: value versus price, and momentum versus risk. One task per
    available analyst, in the horizon's priority order.
    """
    names = ", ".join(request.tickers)
    hypotheses = [
        ThesisHypothesis(
            id="H1",
            statement=f"{names} trade below what their fundamentals and cash flows justify.",
            confirm_if="Intrinsic value estimates exceed the market price with a margin of safety.",
            refute_if="Most valuation methods put intrinsic value below the market price.",
        ),
        ThesisHypothesis(
            id="H2",
            statement=f"Price momentum in {names} is healthy and the risk is acceptable.",
            confirm_if="Trend and momentum point up with normal volatility and no insider selling.",
            refute_if="A downtrend, a volatility spike or heavy insider selling.",
        ),
    ]
    tasks = [
        AnalystTask(
            domain=domain,
            objective=f"Score {names} as of {request.as_of.isoformat()} with the {domain.value} "
            "tools and say which hypothesis the numbers support.",
            hypothesis_ids=["H1", "H2"],
        )
        for domain in HORIZON_ANALYSTS[request.horizon]
        if domain in available
    ]
    return AnalysisPlan(hypotheses=hypotheses, tasks=tasks[:max_tasks])


def _unique[T](items: list[T]) -> list[T]:
    return list(dict.fromkeys(items))
