"""Deterministic compatibility supervisor over isolated DeepAgents personas."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import date
from typing import Any, Protocol, TypedDict

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer
from pydantic import BaseModel, ConfigDict

from fin_graph.agents.middleware.masking import MaskingMiddleware
from fin_graph.config.settings import Settings
from fin_graph.domain.analysts import (
    AnalystDefinition,
    AnalystId,
    analyst_catalog,
    resolve_analysts,
)
from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.quant.common import QuantScore
from fin_graph.domain.quant.personas.registry import scorer_for
from fin_graph.domain.requests.model import AnalysisRequest
from fin_graph.graphs.analysis.personas import build_persona_subagents
from fin_graph.integrations.context import RunContext
from fin_graph.safety.masking import MaskingPolicy


class ToolSource(Protocol):
    def tools(self, context: RunContext) -> list[BaseTool]: ...


class PersonaSnapshotServices(Protocol):
    async def company_snapshot(
        self, ticker: str, as_of: date, context: RunContext
    ) -> CompanySnapshot: ...


class PersonaFinding(BaseModel):
    """One canonical persona's stable score plus its optional DeepAgent narrative."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    analyst_id: AnalystId
    ticker: str
    score: QuantScore | None = None
    reasoning: str = ""
    evidence_ids: tuple[str, ...] = ()
    error: str | None = None


class LegacyAnalysisBundle(BaseModel):
    """Typed result consumed by compatibility transports."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: AnalysisRequest
    findings: tuple[PersonaFinding, ...]


class _CompatState(TypedDict, total=False):
    request: dict[str, Any] | AnalysisRequest
    bundle: dict[str, Any]
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


def make_compat_analysis_agent(
    model: BaseChatModel,
    registry: ToolSource,
    context: RunContext,
    services: PersonaSnapshotServices,
    *,
    checkpointer: Checkpointer = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the full nineteen-persona compatibility analysis graph."""

    definitions = analyst_catalog()
    specs = build_persona_subagents(model, definitions, registry.tools(context))
    options = Settings(_env_file=None)
    masking = MaskingPolicy.from_settings(options) if options.mask_enabled else None
    agents: dict[AnalystId, Any] = {}
    for definition, spec in zip(definitions, specs, strict=True):
        middleware = list(spec.get("middleware", ()))
        if masking is not None:
            middleware.append(MaskingMiddleware(masking))
        agents[definition.id] = create_deep_agent(
            model=model,
            tools=list(spec.get("tools", ())),
            system_prompt=spec.get("system_prompt"),
            middleware=middleware,
            context_schema=RunContext,
            name=spec["name"],
            checkpointer=False,
        )

    async def analyze(state: _CompatState) -> dict[str, Any]:
        request = AnalysisRequest.model_validate(state["request"])
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
                    error=f"{type(snapshot).__name__}: {snapshot}",
                )
            try:
                score = scorer_for(definition.scorer_key)(snapshot)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return PersonaFinding(
                    analyst_id=definition.id,
                    ticker=ticker,
                    error=f"{type(exc).__name__}: {exc}",
                )
            try:
                result = await asyncio.wait_for(
                    agents[definition.id].ainvoke(
                        {
                            "messages": [
                                HumanMessage(content=_score_payload(request, definition, score))
                            ]
                        },
                        context=context,
                    ),
                    timeout=options.llm_timeout_seconds,
                )
                reasoning = _last_text(result)
                error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reasoning = ""
                error = f"{type(exc).__name__}: {exc}"
            return PersonaFinding(
                analyst_id=definition.id,
                ticker=ticker,
                score=score,
                reasoning=reasoning,
                error=error,
            )

        findings = await asyncio.gather(
            *(
                run_persona(ticker, snapshot, definition)
                for ticker, snapshot in zip(request.tickers, snapshots, strict=True)
                for definition in selected
            )
        )
        bundle = LegacyAnalysisBundle(request=request, findings=tuple(findings))
        return {"bundle": bundle.model_dump(mode="json"), "errors": []}

    builder = StateGraph(_CompatState)
    builder.add_node("analyze_personas", analyze)
    builder.add_edge(START, "analyze_personas")
    builder.add_edge("analyze_personas", END)
    return builder.compile(checkpointer=checkpointer)
