"""Deterministic LangGraph spine for a bounded, parallel, point-in-time equity analysis.

``intake → gather_context → plan → analyst x N (Send) → synthesize → verify ⟲ refine →
risk → portfolio → report → deliver``

Every model step has a deterministic fallback, so the run always finishes with a report. The
fallbacks are: the fixed plan, the score-based finding, the score-based thesis and ``hold``. A
thesis that still fails verification after the last wave is replaced by the deterministic one.
Its claims restate scores that are in the evidence, so the report never carries an unverified
model thesis. Risk limits and allowed actions are computed, never chosen by a model. Proposed
orders are paper orders that wait for human approval.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Overwrite, Send

from fin_graph.domain.analysis import (
    AnalysisPlan,
    AnalystFinding,
    AnalystTask,
    constrain_analysis_plan,
    degraded_analyst_finding,
    fallback_analysis_plan,
)
from fin_graph.domain.analysis_report import AnalysisReport, build_analysis_report
from fin_graph.domain.claims import ClaimCheck, ClaimStatus
from fin_graph.domain.evidence import EvidenceItem
from fin_graph.domain.orders import (
    OrderAction,
    PortfolioDecision,
    ProposedOrder,
    clamp_decision,
    compute_allowed_actions,
    hold,
    propose_order,
)
from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.quant.common import Signal
from fin_graph.domain.quant.risk import PositionLimit
from fin_graph.domain.requests.model import AnalysisRequest, RequestSource
from fin_graph.domain.requests.noise import is_trivial_message, request_from_extraction
from fin_graph.domain.requests.parsing import request_from_payload, request_from_text
from fin_graph.domain.thesis import InvestmentThesis, fallback_thesis, suggest_category
from fin_graph.domain.thesis_verification import (
    ThesisVerification,
    missing_sections,
    numeric_check,
    verify_thesis,
)
from fin_graph.domain.tools import Domain, SideEffectLevel, read_tool_meta
from fin_graph.domain.window import AnalysisWindow
from fin_graph.graphs.analysis.analysts import available_analysts, read_tools
from fin_graph.graphs.analysis.evidence import for_ticker, scores_by_domain
from fin_graph.graphs.analysis.services import AnalysisServices, SynthesisContext
from fin_graph.graphs.analysis.state import AnalysisState, SanitizedRequestChannel, latest_findings
from fin_graph.integrations.analytics.engine import AnalyticsEngine
from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.market_data import MarketDataClient
from fin_graph.memory.theses import ThesisMemory, thesis_key
from fin_graph.playbooks.catalog import select_playbooks
from fin_graph.safety.secrets import redact_secrets
from fin_graph.safety.trading_limits import TradingLimits, limit_allowed_actions

#: Claims per ticker the judge reviews; the rest keep their deterministic status.
MAX_JUDGED_CLAIMS = 4
#: A verified thesis below this confidence (0-100) is reported but never traded.
MIN_TRADE_CONFIDENCE = 50
#: Stance → the actions a thesis may lead to. A bearish thesis never buys.
STANCE_ACTIONS: dict[Signal, frozenset[OrderAction]] = {
    Signal.BULLISH: frozenset({OrderAction.BUY, OrderAction.COVER}),
    Signal.BEARISH: frozenset({OrderAction.SELL, OrderAction.SHORT}),
    Signal.NEUTRAL: frozenset(),
}
#: Model steps whose failure the graph absorbs with a deterministic fallback.
FALLBACK_STEPS = ("intake extraction", "planner", "synthesizer", "judge", "portfolio manager")


def is_fallback_error(error: str) -> bool:
    """Whether ``error`` is a model step that fell back, rather than a broken execution."""
    return error.startswith(tuple(f"{step} failed" for step in FALLBACK_STEPS))


class ToolSource(Protocol):
    def tools(self, context: RunContext) -> list[BaseTool]: ...


class AnalysisDelivery(Protocol):
    async def send_analysis(
        self, report: AnalysisReport, destinations: dict[str, str], idempotency_key: str
    ) -> list[Any]: ...


@dataclass(frozen=True)
class AnalysisLimits:
    max_waves: int = 2
    max_tasks_per_wave: int = 6
    max_total_tasks: int = 12
    deadline_seconds: int = 600


DEFAULT_LIMITS = AnalysisLimits()


def _compile_analysis_graph(
    services: AnalysisServices,
    registry: ToolSource,
    *,
    delivery: AnalysisDelivery | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    limits: AnalysisLimits = DEFAULT_LIMITS,
    checkpointer: BaseCheckpointSaver[Any] | Literal[False] | None = None,
    memory: ThesisMemory | None = None,
    portfolio: Portfolio | None = None,
    trading_limits: TradingLimits | None = None,
    orders_today: Callable[[], Awaitable[int]] | None = None,
    instrument: Callable[[str, Callable[..., Any]], Callable[..., Any]] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the analysis graph; ``portfolio`` is the default paper book when none is given."""
    if min(asdict(limits).values()) < 1:
        raise ValueError("analysis limits must be positive")
    gates = trading_limits or TradingLimits()
    default_book = portfolio or Portfolio()

    async def intake(state: AnalysisState) -> dict[str, Any]:
        raw = state["raw_request"]
        if not isinstance(raw, str | dict):
            raise ValueError("invalid analysis input")
        now = clock()
        fresh: dict[str, Any] = {
            "request": {},
            "window": {},
            "integrations": [],
            "available_domains": [],
            "recalled": [],
            "plan": {},
            "tasks": [],
            "findings": Overwrite([]),
            "evidence": Overwrite([]),
            "theses": [],
            "verification": {},
            "playbooks": {},
            "limits": {},
            "allowed": {},
            "decisions": {},
            "orders": [],
            "report": {},
            "deliveries": [],
            "degraded": Overwrite([]),
            "errors": Overwrite([]),
            "delivery_destinations": state.get("destinations") or {},
            "wave": 0,
            "budget": limits.max_total_tasks,
            "started_at": now.isoformat(),
        }
        requested = state.get("user_requested", False)
        errors: list[str] = []
        degraded: list[str] = []
        request: AnalysisRequest | None
        if isinstance(raw, dict) or _is_json_object(raw):
            payload = raw if isinstance(raw, dict) else json.loads(raw)
            try:
                source = RequestSource(state.get("source_hint") or "")
            except ValueError:
                source = None
            try:
                request = request_from_payload(payload, now=now, source=source)
            except ValueError as exc:
                raise ValueError(f"invalid analysis input: {exc}") from None
        else:
            if not requested and is_trivial_message(raw):
                _emit(state, "stage", "noise")
                return {**fresh, "is_noise": True}
            request = request_from_text(raw, now=now)
            if request is None:
                try:
                    timeout = _remaining(fresh["started_at"], clock, limits.deadline_seconds)
                    extraction = await asyncio.wait_for(services.extract(raw), timeout=timeout)
                    request = request_from_extraction(extraction, raw, now=now)
                except Exception as exc:
                    errors.append(f"intake extraction failed: {type(exc).__name__}")
                    degraded.append("intake: no ticker found in the text")
                    request = None
            if request is None:
                _emit(state, "stage", "noise")
                return {**fresh, "is_noise": True, "errors": errors, "degraded": degraded}
        book = state.get("portfolio")
        if book and request.portfolio is None:
            request = request.model_copy(update={"portfolio": Portfolio.model_validate(book)})
        window = AnalysisWindow(as_of=request.as_of)
        _emit(state, "stage", "intake", {"tickers": request.tickers})
        return fresh | {
            "request": request.model_dump(mode="json"),
            "window": window.model_dump(mode="json"),
            "is_noise": False,
            "errors": errors,
            "degraded": degraded,
        }

    async def gather_context(state: AnalysisState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        tools = _safe_tools(registry.tools(_run_context(runtime, registry)))
        analysts = available_analysts(tools)
        integrations = sorted(
            {meta.integration for tool in tools if (meta := read_tool_meta(tool)) is not None}
        )
        request = AnalysisRequest.model_validate(state["request"])
        recalled: list[dict[str, Any]] = []
        errors: list[str] = []
        if memory is not None:
            try:
                for ticker in request.tickers:
                    for item in await memory.search_theses(ticker=ticker, limit=2):
                        recalled.append(
                            {
                                key: item.get(key)
                                for key in (
                                    "ticker",
                                    "as_of",
                                    "stance",
                                    "category",
                                    "confidence",
                                    "summary",
                                    "outcome",
                                )
                            }
                        )
            except Exception as exc:
                errors.append(f"thesis recall failed: {type(exc).__name__}")
        _emit(state, "stage", "gather_context")
        return {
            "integrations": integrations,
            "available_domains": [spec.domain.value for spec in analysts],
            "recalled": recalled,
            "errors": errors,
        }

    async def plan(state: AnalysisState) -> dict[str, Any]:
        request = AnalysisRequest.model_validate(state["request"])
        window = AnalysisWindow.model_validate(state["window"])
        available = [Domain(value) for value in state.get("available_domains", [])]
        findings = [AnalystFinding.model_validate(item) for item in state.get("findings", [])]
        wave = state.get("wave", 0)
        budget = state.get("budget", limits.max_total_tasks)
        max_tasks = min(limits.max_tasks_per_wave, budget)
        errors: list[str] = []
        degraded: list[str] = []
        if wave >= 1:
            # A refinement wave re-runs only what the wider window or a retry can change.
            previous = AnalysisPlan.model_validate(state["plan"])
            chosen = AnalysisPlan(
                hypotheses=previous.hypotheses, tasks=_retry_tasks(state, available)[:max_tasks]
            )
        elif not available:
            chosen = fallback_analysis_plan(request, available, max_tasks)
        else:
            try:
                timeout = _remaining(state["started_at"], clock, limits.deadline_seconds)
                candidate = await asyncio.wait_for(
                    services.plan(request, window, available, findings), timeout=timeout
                )
                chosen = constrain_analysis_plan(candidate, available, request.tickers, max_tasks)
                if not chosen.tasks or not chosen.hypotheses:
                    raise ValueError("empty plan")
            except Exception as exc:
                chosen = fallback_analysis_plan(request, available, max_tasks)
                errors.append(f"planner failed: {type(exc).__name__}")
                degraded.append("plan: deterministic plan")
        _emit(
            state,
            "plan",
            "plan",
            {"wave": wave + 1, "tasks": [task.domain.value for task in chosen.tasks]},
        )
        return {
            "plan": chosen.model_dump(mode="json"),
            "tasks": [task.model_dump(mode="json") for task in chosen.tasks],
            "wave": wave + 1,
            "budget": budget - len(chosen.tasks),
            "errors": errors,
            "degraded": degraded,
        }

    def dispatch(state: AnalysisState) -> list[Send] | str:
        tasks = state.get("tasks", [])
        if not tasks:
            return "synthesize"
        return [
            Send(
                "analyst",
                {
                    "task": task,
                    "plan": state["plan"],
                    "request": state["request"],
                    "window": state["window"],
                    "wave": state["wave"],
                    "started_at": state["started_at"],
                    "thread_id": state.get("thread_id", ""),
                    "run_id": state.get("run_id", ""),
                    "harness": state.get("harness", {}),
                },
            )
            for task in tasks
        ]

    async def analyst(payload: AnalysisState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        task = AnalystTask.model_validate(payload["task"])
        hypotheses = AnalysisPlan.model_validate(payload["plan"]).hypotheses
        request = AnalysisRequest.model_validate(payload["request"])
        window = AnalysisWindow.model_validate(payload["window"])
        context = _run_context(runtime, registry)
        tools = read_tools(_safe_tools(registry.tools(context)), task.domain)
        errors: list[str] = []
        try:
            timeout = _remaining(payload["started_at"], clock, limits.deadline_seconds)
            finding, evidence = await asyncio.wait_for(
                services.analyze(task, hypotheses, request, window, tools, context),
                timeout=timeout,
            )
        except Exception as exc:
            finding = degraded_analyst_finding(task.domain, f"{type(exc).__name__}: unavailable")
            evidence = []
            errors.append(f"{task.domain.value} analyst failed: {type(exc).__name__}")
        _emit(
            payload,
            "analyst",
            task.domain.value,
            {
                "wave": payload["wave"],
                "domain": task.domain.value,
                "evidence_ids": [item.id for item in evidence],
                "signals": {item.ticker: item.signal.value for item in finding.signals},
            },
        )
        return {
            "findings": [{**finding.model_dump(mode="json"), "wave": payload["wave"]}],
            "evidence": [item.model_dump(mode="json") for item in evidence],
            "errors": errors,
            "degraded": [f"{task.domain.value}: score-based finding"] if finding.degraded else [],
        }

    async def synthesize(state: AnalysisState) -> dict[str, Any]:
        request = AnalysisRequest.model_validate(state["request"])
        chosen = AnalysisPlan.model_validate(state["plan"])
        findings = [
            AnalystFinding.model_validate(item)
            for item in latest_findings(state.get("findings", []))
        ]
        evidence = [EvidenceItem.model_validate(item) for item in state.get("evidence", [])]
        previous = {
            ThesisVerification.model_validate(value).ticker: ThesisVerification.model_validate(
                value
            )
            for value in state.get("verification", {}).values()
        }
        theses: list[dict[str, Any]] = []
        chosen_playbooks: dict[str, list[str]] = {}
        errors: list[str] = []
        degraded: list[str] = []
        for ticker in request.tickers:
            scores, ids = scores_by_domain(evidence, ticker)
            category, stance = suggest_category(scores)
            playbooks = select_playbooks(category, scores, question=request.question)
            chosen_playbooks[ticker] = [item["id"] for item in playbooks]
            check = previous.get(ticker)
            if check is not None and check.passed:
                kept = next(
                    (item for item in state.get("theses", []) if item.get("ticker") == ticker), None
                )
                if kept is not None:
                    theses.append(kept)
                    continue
            ticker_evidence = for_ticker(evidence, ticker)
            if not ticker_evidence:
                thesis = fallback_thesis(ticker, scores, ids, findings)
                theses.append(thesis.model_dump(mode="json"))
                degraded.append(f"synthesize {ticker}: no evidence")
                continue
            context = SynthesisContext(
                suggested_category=category,
                suggested_stance=stance.value,
                playbooks=playbooks,
                prior_theses=[
                    item for item in state.get("recalled", []) if item.get("ticker") == ticker
                ],
                previous_problems=_problems(check),
            )
            try:
                timeout = _remaining(state["started_at"], clock, limits.deadline_seconds)
                thesis = await asyncio.wait_for(
                    services.synthesize(
                        ticker, request, chosen, findings, ticker_evidence, context
                    ),
                    timeout=timeout,
                )
            except Exception as exc:
                thesis = fallback_thesis(ticker, scores, ids, findings)
                errors.append(f"synthesizer failed for {ticker}: {type(exc).__name__}")
                degraded.append(f"synthesize {ticker}: score-based thesis")
            theses.append(thesis.model_dump(mode="json"))
        _emit(state, "stage", "synthesize")
        return {
            "theses": theses,
            "playbooks": chosen_playbooks,
            "errors": errors,
            "degraded": degraded,
        }

    async def verify(state: AnalysisState) -> dict[str, Any]:
        evidence = [EvidenceItem.model_validate(item) for item in state.get("evidence", [])]
        by_id = {item.id: item for item in evidence}
        results: dict[str, Any] = {}
        errors: list[str] = []
        for value in state.get("theses", []):
            thesis = InvestmentThesis.model_validate(value)
            report = verify_thesis(thesis, evidence)
            checks = list(report.claims)
            if not thesis.degraded:
                judged = 0
                for index, claim in enumerate(thesis.claims):
                    if claim.source != "model" or checks[index].status is ClaimStatus.UNSUPPORTED:
                        continue
                    # A claim that quotes figures is checked against the data, not by a model.
                    grounded = numeric_check(claim, by_id)
                    if grounded is not None:
                        checks[index] = grounded
                        continue
                    if judged >= MAX_JUDGED_CLAIMS:
                        break
                    judged += 1
                    cited = [by_id[item] for item in claim.evidence_ids]
                    try:
                        timeout = _remaining(state["started_at"], clock, limits.deadline_seconds)
                        judgment = await asyncio.wait_for(
                            services.judge(claim, cited), timeout=timeout
                        )
                        reason = redact_secrets(judgment.reason)[:240]
                        status = (
                            ClaimStatus.SUPPORTED if judgment.supported else ClaimStatus.UNSUPPORTED
                        )
                    except Exception as exc:
                        # The deterministic check already passed; keep it rather than fail closed
                        # on an unavailable judge, and say why.
                        reason = f"{checks[index].reason}; judge unavailable ({type(exc).__name__})"
                        status = checks[index].status
                        errors.append(f"judge failed for {thesis.ticker}: {type(exc).__name__}")
                    checks[index] = checks[index].model_copy(
                        update={"status": status, "reason": reason}
                    )
            report = report.with_claims(checks, thesis)
            results[thesis.ticker] = report.model_dump(mode="json")
        _emit(
            state,
            "verify",
            "verify",
            {
                ticker: {"passed": item["passed"], "score": item["score"]}
                for ticker, item in results.items()
            },
        )
        return {"verification": results, "errors": errors}

    def after_verify(state: AnalysisState) -> str:
        failed = [
            ticker
            for ticker, value in state.get("verification", {}).items()
            if not ThesisVerification.model_validate(value).passed
        ]
        if (
            failed
            and state["wave"] < limits.max_waves
            and state["budget"] > 0
            and _within_deadline(state, clock, limits.deadline_seconds)
        ):
            return "refine"
        return "risk"

    def refine(state: AnalysisState) -> dict[str, Any]:
        window = AnalysisWindow.model_validate(state["window"])
        wider = window.expanded()
        _emit(state, "stage", "refine", {"lookback_days": wider.lookback_days})
        return {"window": wider.model_dump(mode="json")}

    async def risk(state: AnalysisState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        request = AnalysisRequest.model_validate(state["request"])
        evidence = [EvidenceItem.model_validate(item) for item in state.get("evidence", [])]
        findings = [
            AnalystFinding.model_validate(item)
            for item in latest_findings(state.get("findings", []))
        ]
        verification = dict(state.get("verification", {}))
        theses: list[dict[str, Any]] = []
        degraded: list[str] = []
        for value in state.get("theses", []):
            thesis = InvestmentThesis.model_validate(value)
            check = ThesisVerification.model_validate(verification[thesis.ticker])
            if not check.passed and not thesis.degraded:
                pruned = _without_failed_model_claims(thesis, check)
                if pruned is not None:
                    pruned_thesis, kept_checks = pruned
                    recheck = check.model_copy(
                        update={"missing_sections": missing_sections(pruned_thesis)}
                    ).with_claims(kept_checks, pruned_thesis)
                    if recheck.passed:
                        dropped = len(thesis.claims) - len(pruned_thesis.claims)
                        verification[thesis.ticker] = recheck.model_dump(mode="json")
                        degraded.append(
                            f"verify {thesis.ticker}: {dropped} unsupported model claims dropped"
                        )
                        theses.append(pruned_thesis.model_dump(mode="json"))
                        continue
                scores, ids = scores_by_domain(evidence, thesis.ticker)
                thesis = fallback_thesis(thesis.ticker, scores, ids, findings)
                verification[thesis.ticker] = verify_thesis(thesis, evidence).model_dump(
                    mode="json"
                )
                degraded.append(
                    f"verify {thesis.ticker}: model thesis not verified; score-based thesis"
                )
            theses.append(thesis.model_dump(mode="json"))
        book = request.portfolio or default_book
        context = _run_context(runtime, registry)
        errors: list[str] = []
        limits_by_ticker: dict[str, PositionLimit]
        try:
            engine = context.client("analytics", AnalyticsEngine)
            data = context.client(engine.data_integration, object)
            if not isinstance(data, MarketDataClient):
                raise IntegrationError("the market data client is not usable")
            limits_by_ticker = await engine.position_limits(
                request.tickers, request.as_of, data, book
            )
        except (IntegrationError, TypeError) as exc:
            limits_by_ticker = {}
            errors.append(f"risk limits unavailable: {type(exc).__name__}")
            degraded.append("risk: no limits, every ticker holds")
        _emit(state, "stage", "risk")
        return {
            "theses": theses,
            "verification": verification,
            "limits": {
                ticker: item.model_dump(mode="json") for ticker, item in limits_by_ticker.items()
            },
            "errors": errors,
            "degraded": degraded,
        }

    async def portfolio_step(state: AnalysisState) -> dict[str, Any]:
        request = AnalysisRequest.model_validate(state["request"])
        book = request.portfolio or default_book
        limits_by_ticker = {
            ticker: PositionLimit.model_validate(value)
            for ticker, value in state.get("limits", {}).items()
        }
        theses = {
            item["ticker"]: InvestmentThesis.model_validate(item)
            for item in state.get("theses", [])
        }
        verification = {
            ticker: ThesisVerification.model_validate(value)
            for ticker, value in state.get("verification", {}).items()
        }
        prices = {ticker: limit.current_price for ticker, limit in limits_by_ticker.items()}
        allowed_all = compute_allowed_actions(
            request.tickers,
            prices,
            {ticker: limit.max_shares for ticker, limit in limits_by_ticker.items()},
            book,
            allow_short=gates.short_selling_enabled,
        )
        try:
            today_count = await orders_today() if orders_today is not None else 0
        except Exception:
            today_count = gates.max_daily_orders  # unknown count: fail closed
        allowed: dict[str, dict[OrderAction, int]] = {}
        decisions: dict[str, PortfolioDecision] = {}
        orders: list[ProposedOrder] = []
        errors: list[str] = []
        degraded: list[str] = []
        for ticker in request.tickers:
            thesis, limit = theses.get(ticker), limits_by_ticker.get(ticker)
            check = verification.get(ticker)
            if thesis is None or limit is None or limit.error or check is None:
                allowed[ticker] = {OrderAction.HOLD: 0}
                decisions[ticker] = hold("no thesis or risk limit for this ticker")
                continue
            gated = limit_allowed_actions(
                allowed_all.get(ticker, {OrderAction.HOLD: 0}),
                ticker=ticker,
                price=limit.current_price,
                portfolio=book,
                prices=prices,
                limits=gates,
                orders_today=today_count + len(orders),
            )
            consistent = {
                action: maximum
                for action, maximum in gated.items()
                if action is OrderAction.HOLD or action in STANCE_ACTIONS[thesis.stance]
            }
            allowed[ticker] = consistent
            if not check.passed:
                decisions[ticker] = hold("thesis not verified")
                continue
            if thesis.confidence < MIN_TRADE_CONFIDENCE:
                decisions[ticker] = hold(
                    f"confidence {thesis.confidence:.0f}% is below {MIN_TRADE_CONFIDENCE}%",
                    confidence=100,
                )
                continue
            if set(consistent) == {OrderAction.HOLD}:
                decisions[ticker] = hold("no trade the thesis and the limits allow", confidence=100)
                continue
            try:
                timeout = _remaining(state["started_at"], clock, limits.deadline_seconds)
                candidate = await asyncio.wait_for(
                    services.decide(thesis, check.passed, consistent, limit), timeout=timeout
                )
                decision, note = clamp_decision(candidate, consistent)
                if note:
                    degraded.append(f"portfolio {ticker}: {note}")
            except Exception as exc:
                decision = hold("portfolio manager unavailable; holding")
                errors.append(f"portfolio manager failed for {ticker}: {type(exc).__name__}")
                degraded.append(f"portfolio {ticker}: hold fallback")
            decisions[ticker] = decision
            order = propose_order(
                decision,
                ticker=ticker,
                price=limit.current_price,
                as_of=request.as_of,
                run_id=state.get("run_id") or state.get("thread_id") or "local",
                thesis_category=thesis.category,
            )
            if order is not None:
                orders.append(order)
        _emit(
            state,
            "portfolio",
            "portfolio",
            {ticker: decision.action.value for ticker, decision in decisions.items()},
        )
        return {
            "allowed": {
                ticker: {action.value: maximum for action, maximum in actions.items()}
                for ticker, actions in allowed.items()
            },
            "decisions": {
                ticker: item.model_dump(mode="json") for ticker, item in decisions.items()
            },
            "orders": [order.model_dump(mode="json") for order in orders],
            "errors": errors,
            "degraded": degraded,
        }

    async def report(state: AnalysisState) -> dict[str, Any]:
        request = AnalysisRequest.model_validate(state["request"])
        theses = [InvestmentThesis.model_validate(item) for item in state.get("theses", [])]
        evidence = [EvidenceItem.model_validate(item) for item in state.get("evidence", [])]
        started = datetime.fromisoformat(state["started_at"])
        result = build_analysis_report(
            request,
            theses,
            evidence,
            findings=[
                AnalystFinding.model_validate(item)
                for item in latest_findings(state.get("findings", []))
            ],
            verifications={
                ticker: ThesisVerification.model_validate(value)
                for ticker, value in state.get("verification", {}).items()
            },
            limits={
                ticker: PositionLimit.model_validate(value)
                for ticker, value in state.get("limits", {}).items()
            },
            decisions={
                ticker: PortfolioDecision.model_validate(value)
                for ticker, value in state.get("decisions", {}).items()
            },
            orders=[ProposedOrder.model_validate(item) for item in state.get("orders", [])],
            degraded_steps=state.get("degraded", []),
            duration_seconds=(clock() - started).total_seconds(),
        )
        _emit(state, "report", "ready")
        response: dict[str, Any] = {"report": result.model_dump(mode="json")}
        if memory is not None:
            errors: list[str] = []
            run_id = state.get("run_id") or str(uuid4())
            for section in result.sections:
                if section.verification is None or not section.verification.passed:
                    continue
                try:
                    await memory.save_thesis(
                        thesis_key(run_id, section.ticker),
                        ticker=section.ticker,
                        as_of=request.as_of,
                        stance=section.thesis.stance.value,
                        category=section.thesis.category,
                        confidence=section.thesis.confidence,
                        entry_price=section.limit.current_price if section.limit else None,
                        summary=section.thesis.summary,
                        evidence_ids=[
                            item for claim in section.thesis.claims for item in claim.evidence_ids
                        ],
                    )
                except Exception as exc:
                    errors.append(f"thesis memory failed: {type(exc).__name__}")
            if errors:
                response["errors"] = errors
        return response

    async def deliver(state: AnalysisState, config: RunnableConfig) -> dict[str, Any]:
        destinations = state.get("delivery_destinations") or {}
        if not destinations:
            _emit(state, "done", "no delivery requested")
            return {"deliveries": []}
        if delivery is None:
            return {"deliveries": [], "errors": ["delivery not configured"]}
        result = AnalysisReport.model_validate(state["report"])
        errors: list[str] = []
        try:
            execution_id = (
                state.get("run_id")
                or state.get("thread_id")
                or config.get("configurable", {}).get("thread_id")
                or str(uuid4())
            )
            sent = await delivery.send_analysis(result, destinations, execution_id)
            results = [{**asdict(item), "status": str(item.status)} for item in sent]
        except Exception as exc:
            results = []
            errors.append(f"delivery failed: {type(exc).__name__}")
        _emit(state, "done", "delivery complete")
        return {"deliveries": results, "errors": errors}

    graph = StateGraph(AnalysisState, context_schema=RunContext)
    nodes: dict[str, Callable[..., Any]] = {
        "intake": intake,
        "gather_context": gather_context,
        "plan": plan,
        "analyst": analyst,
        "synthesize": synthesize,
        "verify": verify,
        "refine": refine,
        "risk": risk,
        "portfolio": portfolio_step,
        "report": report,
        "deliver": deliver,
    }
    for name, node in nodes.items():
        graph.add_node(name, cast(Any, instrument(name, node) if instrument else node))
    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake", lambda state: END if state.get("is_noise") else "gather_context"
    )
    graph.add_edge("gather_context", "plan")
    graph.add_conditional_edges("plan", dispatch, ["analyst", "synthesize"])
    graph.add_edge("analyst", "synthesize")
    graph.add_edge("synthesize", "verify")
    graph.add_conditional_edges("verify", after_verify, ["refine", "risk"])
    graph.add_edge("refine", "plan")
    graph.add_edge("risk", "portfolio")
    graph.add_edge("portfolio", "report")
    graph.add_edge("report", "deliver")
    graph.add_edge("deliver", END)
    compiled = graph.compile(checkpointer=checkpointer)
    compiled.channels[START] = SanitizedRequestChannel(AnalysisState)
    return compiled


def _retry_tasks(state: Mapping[str, Any], available: Sequence[Domain]) -> list[AnalystTask]:
    """After a failed verification: the technicals (wider window) and analysts that failed."""
    latest = latest_findings(state.get("findings", []))
    failed = [Domain(item["domain"]) for item in latest if item.get("confidence", 0) == 0]
    wanted = [Domain.TECHNICALS, *failed]
    return [
        AnalystTask(
            domain=domain,
            objective="Re-score with the wider window and say what changed.",
            hypothesis_ids=["H1", "H2"],
        )
        for domain in dict.fromkeys(wanted)
        if domain in available
    ]


def _without_failed_model_claims(
    thesis: InvestmentThesis, check: ThesisVerification
) -> tuple[InvestmentThesis, list[ClaimCheck]] | None:
    """The thesis minus the model claims that failed, with the checks of those kept.

    ``None`` when nothing would change. Only model-written claims are dropped; claims restating
    scores always stay. The prose is kept, but every factual claim left in the report is a
    verified one, and the judge's verdicts on the kept claims carry over.
    """
    kept = [
        (claim, result)
        for claim, result in zip(thesis.claims, check.claims, strict=True)
        if claim.source == "scores" or result.status is ClaimStatus.SUPPORTED
    ]
    if len(kept) == len(thesis.claims):
        return None
    return (
        thesis.model_copy(update={"claims": [claim for claim, _ in kept]}),
        [result for _, result in kept],
    )


def _problems(check: ThesisVerification | None) -> list[str]:
    """What the last verification objected to, for the synthesizer's second attempt."""
    if check is None:
        return []
    problems = [f"claim '{item.statement[:120]}': {item.reason}" for item in check.claims
                if item.status is not ClaimStatus.SUPPORTED]  # fmt: skip
    problems += [f"missing section: {name}" for name in check.missing_sections]
    if check.category_mismatch:
        problems.append(f"category: {check.category_mismatch}")
    if check.stance_conflict:
        problems.append(f"stance: {check.stance_conflict}")
    return problems


def _safe_tools(tools: Sequence[BaseTool]) -> list[BaseTool]:
    return [
        tool
        for tool in tools
        if (meta := read_tool_meta(tool)) is not None
        and meta.side_effect_level is SideEffectLevel.READ_ONLY
    ]


def _run_context(runtime: Runtime[RunContext], registry: ToolSource) -> RunContext:
    """Use the caller's clients or build fresh ones for Studio's context-free runs."""
    if isinstance(runtime.context, RunContext):
        return runtime.context
    create = getattr(registry, "run_context", None)
    return create() if callable(create) else RunContext()


def _is_json_object(value: str) -> bool:
    if not value.lstrip().startswith("{"):
        return False
    try:
        return isinstance(json.loads(value), dict)
    except ValueError:
        return False


def _within_deadline(
    state: Mapping[str, Any], clock: Callable[[], datetime], deadline_seconds: int
) -> bool:
    started = datetime.fromisoformat(state["started_at"])
    return (clock() - started).total_seconds() < deadline_seconds


def _remaining(started_at: str, clock: Callable[[], datetime], deadline_seconds: int) -> float:
    remaining = deadline_seconds - (clock() - datetime.fromisoformat(started_at)).total_seconds()
    if remaining <= 0:
        raise TimeoutError("analysis deadline elapsed")
    return remaining


def _emit(
    state: Mapping[str, Any], kind: str, label: str, data: Mapping[str, Any] | None = None
) -> None:
    """Emit progress when LangGraph is streaming; synchronous invokes may ignore it."""
    try:
        writer = get_stream_writer()
        writer(
            {
                "type": kind,
                "thread_id": state.get("thread_id", ""),
                "run_id": state.get("run_id", ""),
                "ts": datetime.now(UTC).isoformat(),
                "data": {"stage": label, **(data or {})},
            }
        )
    except RuntimeError:
        pass
