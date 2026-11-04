"""Deterministic checks on a thesis before anyone sizes an order on it.

Every claim must cite evidence that exists, did not
fail, and is of the kind the claim talks about (a claim about P/E cites metrics, one about RSI
cites indicators). The thesis must be complete, and its category, stance and summary must agree.
The hard conflict is a bullish stance against valuation evidence that puts intrinsic value below
the price.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from fin_graph.domain.claims import ClaimCheck, ClaimStatus
from fin_graph.domain.evidence import EvidenceItem
from fin_graph.domain.quant.common import Signal
from fin_graph.domain.quant.valuation import SIGNAL_GAP
from fin_graph.domain.thesis import InvestmentThesis, ThesisClaim
from fin_graph.domain.thesis_categories import (
    NO_EDGE,
    UNKNOWN,
    category_text_mismatch,
    stance_mismatch,
)
from fin_graph.domain.tools import EvidenceType

MISMATCH_PENALTY = 0.15
#: Words that tie a claim to a kind of evidence; a claim using them should cite that kind.
_SIGNALS: dict[EvidenceType, re.Pattern[str]] = {
    EvidenceType.FINANCIALS: re.compile(
        r"\b(?:revenues?|sales|net income|earnings|eps|free cash flow|fcf|capex|"
        r"capital expenditure|balance sheet|total debt|liabilities|assets|book value|"
        r"dividends?|buybacks?|repurchases?|shares outstanding|ingresos|ventas|"
        r"beneficio neto|utilidad neta|flujo de caja libre|deuda total|pasivos|activos|"
        r"valor contable|dividendos?|recompras?|\bp/?e\b|\bp/?b\b|\bp/?s\b|\broe\b|"
        r"\broic\b|\broa\b|\bmargins?\b|current ratio|"
        r"debt[- ]to[- ]equity|\bd/e\b|\bpeg\b|ev/ebitda|return on equity|interest coverage|"
        r"\bm[aá]rgen(?:es)?\b|rentabilidad sobre (?:el )?patrimonio|ratio corriente|"
        r"deuda (?:sobre|a) patrimonio)"
    ),
    EvidenceType.PRICES: re.compile(
        r"\b(?:closed at|share price|stock price|traded at|52-week|drawdown|all-time high|"
        r"price (?:fell|rose|dropped|gained))\b"
    ),
    EvidenceType.INDICATORS: re.compile(
        r"\b(?:rsi|moving averages?|ema|sma|adx|bollinger|momentum|uptrend|downtrend|"
        r"volatility|atr|hurst|z-score|medias? m[oó]vil(?:es)?|volatilidad|tendencia "
        r"(?:alcista|bajista))\b"
    ),
    EvidenceType.NEWS: re.compile(
        r"\b(?:headlines?|news|articles?|press|reported that|titulares?|noticias?|prensa)\b"
    ),
    EvidenceType.INSIDER: re.compile(
        r"\b(?:insiders?|form 4|directors? (?:bought|sold)|insider (?:buying|selling|sales)|"
        r"directivos? (?:compraron|vendieron))\b"
    ),
    EvidenceType.VALUATION_MODEL: re.compile(
        r"\b(?:dcf|intrinsic value|discounted cash flow|owner earnings|graham number|"
        r"margin of safety|net-net|ncav|residual income|wacc|fair value|valor intr[ií]nseco|"
        r"margen de seguridad|flujos? descontados?|n[uú]mero de graham|valor razonable)\b"
    ),
}


class ThesisVerification(BaseModel):
    """What the deterministic checks found for one ticker's thesis, and the resulting score.

    ``passed`` means the thesis is sound: complete, every claim supported, its category agreeing
    with its stance and no conflict with the valuation evidence. It does not require
    confidence. A sound thesis may still be too weak to trade; the portfolio step applies that
    gate. ``score`` (confidence times claim backing, minus a penalty for a summary that reads as
    another category) is reported, not used to pass.
    """

    ticker: str
    claims: list[ClaimCheck]
    missing_sections: list[str]
    category_mismatch: str | None = None
    stance_mismatch: str | None = None
    stance_conflict: str | None = None
    score: float
    passed: bool

    def with_claims(self, claims: list[ClaimCheck], thesis: InvestmentThesis) -> ThesisVerification:
        """Reassess the same structural checks after semantic claim judgments."""
        supported = sum(check.status is ClaimStatus.SUPPORTED for check in claims)
        weak = sum(check.status is ClaimStatus.WEAK for check in claims)
        if claims:
            backing = (supported + 0.5 * weak) / len(claims)
        else:
            backing = 1.0 if thesis.category in (NO_EDGE, UNKNOWN) else 0.0
        score = thesis.confidence / 100 * backing - (
            MISMATCH_PENALTY if self.category_mismatch else 0.0
        )
        score = round(min(max(score, 0.0), 1.0), 2)
        passed = (
            not self.missing_sections
            and not self.stance_conflict
            and not self.stance_mismatch
            and all(check.status is ClaimStatus.SUPPORTED for check in claims)
        )
        return self.model_copy(update={"claims": claims, "score": score, "passed": passed})


def check_claim(claim: ThesisClaim, evidence: dict[str, EvidenceItem]) -> ClaimCheck:
    """Whether ``claim`` cites evidence that exists, succeeded and is of the kind it talks about."""
    if not claim.evidence_ids:
        return ClaimCheck(
            statement=claim.statement, status=ClaimStatus.UNSUPPORTED, reason="cites no evidence"
        )
    missing = [item for item in claim.evidence_ids if item not in evidence]
    if missing:
        return ClaimCheck(
            statement=claim.statement,
            status=ClaimStatus.UNSUPPORTED,
            reason=f"cites evidence that was never collected: {', '.join(missing)}",
        )
    cited = [evidence[item] for item in claim.evidence_ids]
    failed = [item.id for item in cited if item.error]
    if failed:
        return ClaimCheck(
            statement=claim.statement,
            status=ClaimStatus.UNSUPPORTED,
            reason=f"cites failed tool calls: {', '.join(failed)}",
        )
    text = claim.statement.lower()
    mentioned = {kind for kind, pattern in _SIGNALS.items() if pattern.search(text)}
    cited_kinds = {item.evidence_type for item in cited}
    if mentioned and not mentioned & cited_kinds:
        wanted = ", ".join(sorted(kind.value for kind in mentioned))
        return ClaimCheck(
            statement=claim.statement,
            status=ClaimStatus.WEAK,
            reason=f"talks about {wanted} but cites none of that kind",
        )
    return ClaimCheck(statement=claim.statement, status=ClaimStatus.SUPPORTED, reason="ok")


#: A number in a claim: "148.8%", "39.68", "-67,0 %", "1,696,953". Years are not figures.
_NUMBER = re.compile(r"(?<![\w.])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:[.,]\d+)?)\s*(%)?")
_YEAR = re.compile(r"(?:19|20)\d{2}")
#: Relative tolerance: a claim may round the figure it quotes.
GROUNDING_TOLERANCE = 0.006


def _claim_numbers(statement: str) -> list[tuple[str, float, bool]]:
    """``(text, value, is_percent)`` for every figure the claim quotes."""
    found = []
    for match in _NUMBER.finditer(statement):
        text, percent = match.group(1), bool(match.group(2))
        if _YEAR.fullmatch(text) and not percent:
            continue
        if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
            value = float(text.replace(",", ""))
        else:
            value = float(text.replace(",", "."))
        found.append((match.group(0).strip(), value, percent))
    return found


def _evidence_numbers(value: Any, out: list[float]) -> None:
    """Every number in an artifact, including the ones written inside its text fields."""
    if isinstance(value, bool):
        return
    if isinstance(value, int | float):
        out.append(float(value))
    elif isinstance(value, str):
        for _text, number, percent in _claim_numbers(value):
            out.append(number / 100 if percent else number)
    elif isinstance(value, dict):
        for item in value.values():
            _evidence_numbers(item, out)
    elif isinstance(value, list):
        for item in value:
            _evidence_numbers(item, out)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(GROUNDING_TOLERANCE * max(abs(a), abs(b)), 1e-9)


def ground_numbers(statement: str, cited: Iterable[EvidenceItem]) -> tuple[list[str], list[str]]:
    """Which figures of ``statement`` appear in the cited evidence, and which do not.

    A percentage matches its fraction (148.8% ↔ 1.488) or itself; any figure tolerates the
    rounding of a quote. This is how a claim written by a small model is checked: the number
    must be in the data it cites, whatever the wording around it.
    """
    values: list[float] = []
    for item in cited:
        _evidence_numbers(item.data, values)
        _evidence_numbers(item.summary, values)
    grounded, missing = [], []
    for text, number, percent in _claim_numbers(statement):
        if not percent and number.is_integer() and abs(number) < 10:
            continue  # small counts appear in almost any artifact; they prove nothing
        targets = [number / 100, number] if percent else [number, number / 100, number * 100]
        if any(_close(target, value) for target in targets for value in values):
            grounded.append(text)
        else:
            missing.append(text)
    return grounded, missing


def numeric_check(claim: ThesisClaim, evidence: dict[str, EvidenceItem]) -> ClaimCheck | None:
    """A deterministic verdict for a claim that quotes figures; ``None`` when it quotes none."""
    cited = [evidence[item] for item in claim.evidence_ids if item in evidence]
    if not cited or any(item.error for item in cited):
        return None
    grounded, missing = ground_numbers(claim.statement, cited)
    if not grounded and not missing:
        return None
    if missing:
        return ClaimCheck(
            statement=claim.statement,
            status=ClaimStatus.UNSUPPORTED,
            reason=f"figures not in the cited evidence: {', '.join(missing)}",
        )
    return ClaimCheck(
        statement=claim.statement,
        status=ClaimStatus.SUPPORTED,
        reason=f"figures found in the cited evidence: {', '.join(grounded)}",
    )


def missing_sections(thesis: InvestmentThesis) -> list[str]:
    """Sections the thesis left empty; a concluded thesis needs both cases and its risks."""
    concluded = thesis.category not in (NO_EDGE, UNKNOWN)
    required = {
        "summary": bool(thesis.summary.strip()),
        "bull_case": bool(thesis.bull_case) or not concluded,
        "bear_case": bool(thesis.bear_case) or not concluded,
        "risks": bool(thesis.risks),
        "claims": bool(thesis.claims) or not concluded,
    }
    return [name for name, present in required.items() if not present]


def _valuation_gap(item: EvidenceItem) -> float | None:
    """The weighted intrinsic-value gap a valuation score carries, if this item is one."""
    data: Any = item.data
    if item.evidence_type is not EvidenceType.VALUATION_MODEL or not isinstance(data, dict):
        return None
    if data.get("analyst") != "valuation" or data.get("ticker") is None:
        return None
    gap = data.get("score")
    return float(gap) if isinstance(gap, int | float) and not isinstance(gap, bool) else None


def stance_conflict(thesis: InvestmentThesis, evidence: Iterable[EvidenceItem]) -> str | None:
    """A bullish thesis against valuation evidence that prices the stock above intrinsic value."""
    if thesis.stance is not Signal.BULLISH:
        return None
    for item in evidence:
        data: Any = item.data
        gap = _valuation_gap(item)
        if gap is not None and data.get("ticker") == thesis.ticker and gap < -SIGNAL_GAP:
            return (
                f"a bullish stance contradicts the valuation evidence {item.id}: intrinsic value "
                f"is {abs(gap):.0%} below the market price"
            )
    return None


def verify_thesis(thesis: InvestmentThesis, evidence: Iterable[EvidenceItem]) -> ThesisVerification:
    """Check citations, completeness, category/stance/summary agreement and the valuation conflict.

    The score is the thesis confidence (as a fraction) scaled by how well its claims are backed
    (a weak claim counts half), minus a penalty when the summary reads as another category. With
    no claims, only ``no_edge`` and ``unknown`` keep their confidence. A category that implies
    the other stance fails the thesis outright.
    """
    items = list(evidence)
    by_id = {item.id: item for item in items}
    checks = [check_claim(claim, by_id) for claim in thesis.claims]
    return ThesisVerification(
        ticker=thesis.ticker,
        claims=checks,
        missing_sections=missing_sections(thesis),
        category_mismatch=category_text_mismatch(thesis.summary, thesis.category),
        stance_mismatch=stance_mismatch(thesis.category, thesis.stance),
        stance_conflict=stance_conflict(thesis, items),
        score=0.0,
        passed=False,
    ).with_claims(checks, thesis)
