"""The analysis report: per-ticker thesis, signals, risk limit and order, evidence as E1, E2."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from pydantic import BaseModel, Field

from fin_graph.domain.analysis import AnalystFinding, TickerSignal
from fin_graph.domain.claims import ClaimStatus
from fin_graph.domain.evidence import EvidenceItem
from fin_graph.domain.orders import PortfolioDecision, ProposedOrder
from fin_graph.domain.quant.risk import PositionLimit
from fin_graph.domain.requests.model import AnalysisRequest
from fin_graph.domain.thesis import InvestmentThesis
from fin_graph.domain.thesis_verification import ThesisVerification

DISCLAIMER = (
    "Uso educativo y paper trading: no es asesoría financiera ni una recomendación de "
    "inversión. Ninguna orden se ejecuta sin aprobación humana y nunca contra un broker real."
)


class CitedClaim(BaseModel):
    """A claim as the report shows it: its citations as labels, and any doubt about it."""

    statement: str
    citations: list[str] = Field(default_factory=list)
    status: ClaimStatus | None = None
    reason: str = ""


class TickerSection(BaseModel):
    """Everything the report says about one ticker."""

    ticker: str
    thesis: InvestmentThesis
    verification: ThesisVerification | None = None
    claims: list[CitedClaim] = Field(default_factory=list)
    signals: dict[str, TickerSignal] = Field(default_factory=dict)
    limit: PositionLimit | None = None
    decision: PortfolioDecision | None = None
    order: ProposedOrder | None = None


class AnalysisReport(BaseModel):
    """One analysis' outcome, ready to render for any channel."""

    request: AnalysisRequest
    sections: list[TickerSection]
    evidence: list[EvidenceItem] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    degraded_steps: list[str] = Field(default_factory=list)
    duration_seconds: float | None = None
    disclaimer: str = DISCLAIMER

    @property
    def title(self) -> str:
        return self.request.title

    @property
    def orders(self) -> list[ProposedOrder]:
        """The proposed orders, in ticker order."""
        return [section.order for section in self.sections if section.order is not None]


def build_analysis_report(
    request: AnalysisRequest,
    theses: Sequence[InvestmentThesis],
    evidence: Iterable[EvidenceItem],
    *,
    findings: Sequence[AnalystFinding] = (),
    verifications: Mapping[str, ThesisVerification] | None = None,
    limits: Mapping[str, PositionLimit] | None = None,
    decisions: Mapping[str, PortfolioDecision] | None = None,
    orders: Iterable[ProposedOrder] = (),
    degraded_steps: Iterable[str] = (),
    duration_seconds: float | None = None,
) -> AnalysisReport:
    """Assemble the report: cited evidence only, labelled in order of first citation.

    Sections follow the request's ticker order; a ticker without a thesis is left out.
    Citations of evidence that does not exist are dropped from the labels (verification
    already flags those claims as unsupported).
    """
    by_id = {item.id: item for item in evidence}
    by_ticker = {thesis.ticker: thesis for thesis in theses}
    verified = verifications or {}
    order_by_ticker = {order.ticker: order for order in orders}
    labels: dict[str, str] = {}
    sections = []
    for ticker in request.tickers:
        thesis = by_ticker.get(ticker)
        if thesis is None:
            continue
        for claim in thesis.claims:
            for item in claim.evidence_ids:
                if item in by_id and item not in labels:
                    labels[item] = f"E{len(labels) + 1}"
        check = verified.get(ticker)
        checks = check.claims if check is not None else []
        sections.append(
            TickerSection(
                ticker=ticker,
                thesis=thesis,
                verification=check,
                claims=[
                    CitedClaim(
                        statement=claim.statement,
                        citations=[labels[item] for item in claim.evidence_ids if item in labels],
                        status=checks[index].status if index < len(checks) else None,
                        reason=checks[index].reason if index < len(checks) else "",
                    )
                    for index, claim in enumerate(thesis.claims)
                ],
                signals={
                    finding.domain.value: signal
                    for finding in findings
                    if (signal := finding.signal_for(ticker)) is not None
                },
                limit=(limits or {}).get(ticker),
                decision=(decisions or {}).get(ticker),
                order=order_by_ticker.get(ticker),
            )
        )
    return AnalysisReport(
        request=request,
        sections=sections,
        evidence=[by_id[item] for item in labels],
        labels=labels,
        degraded_steps=list(dict.fromkeys(degraded_steps)),
        duration_seconds=duration_seconds,
    )
