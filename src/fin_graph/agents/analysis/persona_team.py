"""Deterministic supervisor over nineteen isolated DeepAgents personas."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date
from enum import StrEnum
from typing import Any, Protocol, TypedDict

from deepagents import create_deep_agent
from deepagents.middleware.subagents import SubAgent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer
from pydantic import BaseModel, ConfigDict

from fin_graph.agents.analysis.personas import (
    PERSONA_TOOL_SCOPE,
    PersonaToolScope,
    build_persona_subagents,
)
from fin_graph.agents.catalog import AgentDescriptor
from fin_graph.agents.common.middleware.masking import MaskingMiddleware
from fin_graph.config.settings import Settings
from fin_graph.domain.analysts import (
    AnalystDefinition,
    AnalystId,
    analyst_catalog,
    resolve_analysts,
)
from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.quant.common import QuantScore
from fin_graph.domain.quant.personas.registry import scorer_for
from fin_graph.domain.requests.model import AnalysisRequest
from fin_graph.integrations.context import RunContext
from fin_graph.safety.masking import MaskingPolicy


class ToolSource(Protocol):
    def tools(self, context: RunContext) -> list[BaseTool]: ...


class PersonaSnapshotServices(Protocol):
    async def company_snapshot(
        self, ticker: str, as_of: date, context: RunContext
    ) -> CompanySnapshot: ...


class PersonaResultBuilder(Protocol):
    def __call__(
        self,
        findings: Sequence[PersonaFinding],
        portfolio: Portfolio,
        prices: Mapping[str, float],
        *,
        price_histories: Mapping[str, Sequence[Any]] | None = None,
    ) -> Any: ...


class PersonaFinding(BaseModel):
    """One canonical persona's stable score plus its optional DeepAgent narrative."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    analyst_id: AnalystId
    ticker: str
    score: QuantScore | None = None
    reasoning: str = ""
    evidence_ids: tuple[str, ...] = ()
    error: PersonaError | None = None


class PersonaFailureCode(StrEnum):
    DATA_UNAVAILABLE = "data_unavailable"
    SCORER_FAILED = "scorer_failed"
    NARRATION_FAILED = "narration_failed"


class PersonaError(BaseModel):
    """Public failure record; raw provider/model exception text never crosses the boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: PersonaFailureCode
    message: str
    correlation_id: str


class PersonaAnalysisBundle(BaseModel):
    """Typed result shared by product and external adapters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: AnalysisRequest
    findings: tuple[PersonaFinding, ...]


class _PersonaTeamState(TypedDict, total=False):
    request: dict[str, Any] | AnalysisRequest
    raw_request: dict[str, Any]
    portfolio: dict[str, Any]
    bundle: dict[str, Any]
    result: dict[str, Any]
    errors: list[str]


def _last_text(result: Mapping[str, Any]) -> str:
    messages = result.get("messages")
    if not isinstance(messages, list | tuple) or not messages:
        return ""
    content = getattr(messages[-1], "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def _score_payload(
    request: AnalysisRequest,
    definition: AnalystDefinition,
    score: QuantScore,
) -> str:
    return json.dumps(
        {
            "task": "Explain this authoritative deterministic score without changing it.",
            "analyst_id": definition.id.value,
            "ticker": score.ticker,
            "as_of": request.as_of.isoformat(),
            "question": request.question,
            "score": score.model_dump(mode="json"),
        },
        ensure_ascii=False,
    )


def _public_error(
    code: PersonaFailureCode,
    definition: AnalystDefinition,
    ticker: str,
    exc: Exception,
) -> PersonaError:
    digest = hashlib.sha256(
        f"{code.value}:{definition.id.value}:{ticker}:{type(exc).__name__}".encode()
    ).hexdigest()[:16]
    messages = {
        PersonaFailureCode.DATA_UNAVAILABLE: "Company data is temporarily unavailable.",
        PersonaFailureCode.SCORER_FAILED: "The deterministic analysis could not be completed.",
        PersonaFailureCode.NARRATION_FAILED: (
            "The deterministic score is available without a narrative."
        ),
    }
    return PersonaError(code=code, message=messages[code], correlation_id=digest)


def _evidence_ids(snapshot: CompanySnapshot) -> tuple[str, ...]:
    values = [
        *(f"price:{item.day.isoformat()}" for item in snapshot.prices),
        *(f"metric:{item.report_period.isoformat()}" for item in snapshot.metrics),
        *(f"line-item:{item.report_period.isoformat()}" for item in snapshot.line_items),
        *(f"news:{item.url or item.date.isoformat()}" for item in snapshot.news),
        *(f"insider:{item.filing_date.isoformat()}" for item in snapshot.insider_trades),
    ]
    return tuple(dict.fromkeys(values))


def _authoritative_reasoning(text: str, score: QuantScore) -> str:
    cleaned = text.strip()
    bullish_actions = (
        r"\bbullish\b",
        r"\bbuy\b",
        r"\b(?:go|stay|remain|open(?:\s+a)?)\s+long\b",
        r"\blong\s+(?:the|this|position|shares|stock|ticker)\b",
        r"\boverweight\b",
    )
    bearish_actions = (
        r"\bbearish\b",
        r"\bsell\b",
        r"\b(?:go|stay|remain|open(?:\s+a)?)\s+short\b",
        r"\bshort\s+(?:the|this|position|shares|stock|ticker)\b",
        r"\bunderweight\b",
    )
    forbidden = {
        "bullish": bearish_actions,
        "bearish": bullish_actions,
        "neutral": (*bullish_actions, *bearish_actions),
    }[score.signal.value]
    if any(re.search(pattern, cleaned, flags=re.IGNORECASE) for pattern in forbidden):
        return (
            f"Deterministic {score.signal.value} score retained; "
            "the model narrative was withheld because it contradicted that score."
        )
    return cleaned


def make_persona_agent(
    model: BaseChatModel,
    spec: SubAgent,
    *,
    masking: MaskingPolicy | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build one isolated persona from its declared tools, prompt, and middleware."""

    middleware = list(spec.get("middleware", ()))
    if masking is not None:
        middleware.append(MaskingMiddleware(masking))
    return create_deep_agent(
        model=model,
        tools=list(spec.get("tools", ())),
        system_prompt=spec.get("system_prompt"),
        middleware=middleware,
        context_schema=RunContext,
        name=spec["name"],
        checkpointer=False,
    )


def make_persona_analysis_supervisor(
    model: BaseChatModel,
    registry: ToolSource,
    context: RunContext,
    services: PersonaSnapshotServices,
    *,
    checkpointer: Checkpointer = None,
    result_builder: PersonaResultBuilder | None = None,
    settings: Settings | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the full nineteen-persona analysis graph."""

    definitions = analyst_catalog()
    specs = build_persona_subagents(model, definitions, registry.tools(context))
    options = settings or Settings(_env_file=None)
    model_semaphore = asyncio.Semaphore(options.llm_max_concurrency)
    masking = MaskingPolicy.from_settings(options) if options.mask_enabled else None
    agents: dict[AnalystId, Any] = {}
    for definition, spec in zip(definitions, specs, strict=True):
        agents[definition.id] = make_persona_agent(model, spec, masking=masking)

    async def analyze(state: _PersonaTeamState) -> dict[str, Any]:
        request_payload = state.get("request")
        portfolio_payload: object | None = state.get("portfolio")
        if request_payload is None:
            raw = state.get("raw_request", {})
            request_payload = {
                "tickers": raw.get("tickers", ()),
                "as_of": raw.get("as_of") or date.today(),
                "horizon": raw.get("horizon", "medium"),
                "question": raw.get("question", ""),
                "analysts": raw.get("analysts"),
                "source": "chat",
                "portfolio": state.get("portfolio"),
            }
        elif isinstance(request_payload, Mapping):
            portfolio_payload = request_payload.get("portfolio", portfolio_payload)
        request = AnalysisRequest.model_validate(request_payload)
        selected = resolve_analysts(request.analysts)

        async def load(ticker: str) -> CompanySnapshot | Exception:
            try:
                return await services.company_snapshot(ticker, request.as_of, context)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return exc

        snapshots = await asyncio.gather(*(load(ticker) for ticker in request.tickers))

        async def run_persona(
            ticker: str,
            snapshot: CompanySnapshot | Exception,
            definition: AnalystDefinition,
        ) -> PersonaFinding:
            if isinstance(snapshot, Exception):
                return PersonaFinding(
                    analyst_id=definition.id,
                    ticker=ticker,
                    error=_public_error(
                        PersonaFailureCode.DATA_UNAVAILABLE,
                        definition,
                        ticker,
                        snapshot,
                    ),
                )
            try:
                score = scorer_for(definition.scorer_key)(snapshot)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return PersonaFinding(
                    analyst_id=definition.id,
                    ticker=ticker,
                    error=_public_error(
                        PersonaFailureCode.SCORER_FAILED,
                        definition,
                        ticker,
                        exc,
                    ),
                )
            try:
                token = PERSONA_TOOL_SCOPE.set(PersonaToolScope(ticker=ticker, as_of=request.as_of))
                try:
                    async with model_semaphore:
                        result = await asyncio.wait_for(
                            agents[definition.id].ainvoke(
                                {
                                    "messages": [
                                        HumanMessage(
                                            content=_score_payload(request, definition, score)
                                        )
                                    ]
                                },
                                context=context,
                            ),
                            timeout=options.llm_timeout_seconds,
                        )
                finally:
                    PERSONA_TOOL_SCOPE.reset(token)
                reasoning = _authoritative_reasoning(_last_text(result), score)
                error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reasoning = ""
                error = _public_error(
                    PersonaFailureCode.NARRATION_FAILED,
                    definition,
                    ticker,
                    exc,
                )
            return PersonaFinding(
                analyst_id=definition.id,
                ticker=ticker,
                score=score,
                reasoning=reasoning,
                evidence_ids=_evidence_ids(snapshot),
                error=error,
            )

        findings = await asyncio.gather(
            *(
                run_persona(ticker, snapshot, definition)
                for ticker, snapshot in zip(request.tickers, snapshots, strict=True)
                for definition in selected
            )
        )
        bundle = PersonaAnalysisBundle(request=request, findings=tuple(findings))
        output: dict[str, Any] = {
            "bundle": bundle.model_dump(mode="json"),
            "errors": [],
        }
        if result_builder is None:
            return output

        portfolio = (
            Portfolio.model_validate(portfolio_payload)
            if portfolio_payload is not None
            else request.portfolio or Portfolio()
        )
        prices: dict[str, float] = {}
        price_histories: dict[str, tuple[Any, ...]] = {}
        for ticker, snapshot in zip(request.tickers, snapshots, strict=True):
            if isinstance(snapshot, Exception):
                continue
            history = tuple(
                sorted(
                    (bar for bar in snapshot.prices if bar.day <= request.as_of),
                    key=lambda bar: bar.time,
                )
            )
            price_histories[ticker] = history
            if history:
                prices[ticker] = history[-1].effective_close
        result = result_builder(
            findings,
            portfolio,
            prices,
            price_histories=price_histories,
        )
        output["result"] = (
            result.model_dump(mode="json") if isinstance(result, BaseModel) else result
        )
        return output

    builder = StateGraph(_PersonaTeamState)
    builder.add_node("analyze_personas", analyze)
    builder.add_edge(START, "analyze_personas")
    builder.add_edge("analyze_personas", END)
    return builder.compile(checkpointer=checkpointer)


_PERSONA_IDS = tuple(f"analysis.persona.{item.id.value}" for item in analyst_catalog())
PERSONA_TEAM_DESCRIPTORS = (
    AgentDescriptor(
        id="analysis.persona_team",
        display_name="Financial persona team",
        kind="deepagents_supervisor",
        purpose="Run the selected deterministic financial personas and retain their scores.",
        factory_path=("fin_graph.agents.analysis.persona_team:make_persona_analysis_supervisor"),
        children=_PERSONA_IDS,
        tool_policy="read_only",
        middleware=("MaskingMiddleware",),
        limits=("19 known personas", "configured LLM timeout per narration"),
        activation="Reference-client analysis and the chat analysis tool.",
        surfaces=("reference Python API", "reference HTTP API", "chat"),
    ),
    *(
        AgentDescriptor(
            id=f"analysis.persona.{definition.id.value}",
            display_name=definition.display_name,
            kind="deepagents_subagent",
            purpose=f"{definition.description}. {definition.investing_style}",
            factory_path="fin_graph.agents.analysis.persona_team:make_persona_agent",
            tool_policy="read_only",
            middleware=(
                "PersonaPointInTimeMiddleware",
                "EvidenceGateMiddleware",
                "ModelCallLimitMiddleware",
                "DisclaimerMiddleware",
            ),
            limits=("8 model calls", "request ticker/date boundary"),
            activation=f"Selected persona: {definition.id.value}.",
            surfaces=("persona team",),
        )
        for definition in analyst_catalog()
    ),
)
