"""Deterministic scores for one equity scenario run.

Per ticker: did the thesis take the expected stance and category, avoid the forbidden ones,
mention what it should, pass verification, and (when the scenario says) choose the expected
action. Per run: valid citations, the analysis trajectory against the optimal one, waves,
degraded steps, latency and tokens. Nothing here asks a model.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from fin_graph.evals.market.scenarios import EquityScenario, TickerAnswer
from fin_graph.workflows.analysis.graph import is_fallback_error


class TickerScore(BaseModel):
    ticker: str
    expected_signal: str
    signal: str | None
    signal_match: bool
    expected_category: str
    category: str | None
    category_match: bool
    forbidden_category: bool
    keywords_found: int
    keywords_total: int
    verified: bool
    degraded: bool
    expected_action: str | None = None
    action: str | None = None
    action_match: bool | None = None


class EquityScore(BaseModel):
    scenario: str
    tickers: list[TickerScore]
    signal_accuracy: float
    category_accuracy: float
    verified_share: float
    citations_valid: int
    citations_total: int
    trajectory_covered: int
    trajectory_total: int
    trajectory_extra: int
    required_queries_found: int
    required_queries_total: int
    waves: int
    degraded_steps: int
    graph_errors: int
    latency_seconds: float
    tokens: int | None

    @property
    def passed(self) -> bool:
        """Every ticker got its stance and category right without a forbidden category."""
        return all(
            item.signal_match and item.category_match and not item.forbidden_category
            for item in self.tickers
        )


def _thesis_text(thesis: Mapping[str, Any]) -> str:
    parts = [
        thesis.get("summary", ""),
        *(thesis.get("bull_case") or []),
        *(thesis.get("bear_case") or []),
        *(thesis.get("risks") or []),
        *(thesis.get("catalysts") or []),
        *(claim.get("statement", "") for claim in thesis.get("claims") or []),
    ]
    return " ".join(str(part) for part in parts).casefold()


def _found(keyword: str, text: str) -> bool:
    return any(option.strip().casefold() in text for option in keyword.split("|"))


def _ticker_score(ticker: str, answer: TickerAnswer, state: Mapping[str, Any]) -> TickerScore:
    thesis: dict[str, Any] = next(
        (item for item in state.get("theses", []) if item.get("ticker") == ticker), {}
    )
    check = (state.get("verification") or {}).get(ticker) or {}
    decision = (state.get("decisions") or {}).get(ticker) or {}
    category = thesis.get("category")
    signal = thesis.get("stance")
    text = _thesis_text(thesis)
    action = decision.get("action")
    return TickerScore(
        ticker=ticker,
        expected_signal=answer.expected_signal.value,
        signal=signal,
        signal_match=signal == answer.expected_signal.value,
        expected_category=answer.thesis_category,
        category=category,
        category_match=category in {answer.thesis_category, *answer.equivalent_categories},
        forbidden_category=category in answer.forbidden_categories,
        keywords_found=sum(_found(word, text) for word in answer.required_keywords),
        keywords_total=len(answer.required_keywords),
        verified=bool(check.get("passed")),
        degraded=bool(thesis.get("degraded")),
        expected_action=answer.expected_action,
        action=action,
        action_match=None if answer.expected_action is None else action == answer.expected_action,
    )


def score_equity_scenario(
    scenario: EquityScenario,
    state: Mapping[str, Any],
    *,
    elapsed: float,
    tokens: int | None = None,
) -> EquityScore:
    """Score a finished analysis state; failed tool calls never count as valid citations."""
    answer = scenario.answer
    tickers = [_ticker_score(ticker, item, state) for ticker, item in answer.tickers.items()]
    evidence = state.get("evidence", [])
    valid = {item["id"] for item in evidence if not item.get("error")}
    citations = [
        evidence_id
        for thesis in state.get("theses", [])
        for claim in thesis.get("claims") or []
        for evidence_id in claim.get("evidence_ids") or []
    ]
    observed = [str(item.get("tool")) for item in evidence if not item.get("error")]
    distinct = set(observed)
    expected = set(answer.optimal_trajectory)
    waves = [int(item.get("wave", 0)) for item in state.get("findings", [])]
    count = max(len(tickers), 1)
    return EquityScore(
        scenario=scenario.key,
        tickers=tickers,
        signal_accuracy=round(sum(item.signal_match for item in tickers) / count, 3),
        category_accuracy=round(sum(item.category_match for item in tickers) / count, 3),
        verified_share=round(sum(item.verified for item in tickers) / count, 3),
        citations_valid=sum(citation in valid for citation in citations),
        citations_total=len(citations),
        trajectory_covered=len(expected & distinct),
        trajectory_total=len(expected),
        trajectory_extra=len(distinct - expected),
        required_queries_found=sum(query in distinct for query in answer.required_queries),
        required_queries_total=len(answer.required_queries),
        waves=max(waves, default=int(state.get("wave", 0))),
        degraded_steps=len(state.get("degraded", [])),
        graph_errors=sum(not is_fallback_error(str(e)) for e in state.get("errors", [])),
        latency_seconds=round(elapsed, 3),
        tokens=tokens,
    )
