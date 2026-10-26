"""Ask the chat model to reconsider an unsupported investment recommendation once.

A recommendation ("compra", "you should sell") or a price prediction ("llegará a 200",
"target price") needs evidence from this turn: an analysis run, an analyst, a compute tool or an
order preview. Without it, the model gets one nudge to cite evidence or to state the idea as a
conditional hypothesis. Price targets are never acceptable, with or without evidence.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

ModelCallResult = ModelResponse[Any] | AIMessage | ExtendedModelResponse[Any]

_RECOMMENDATION = re.compile(
    r"\b(?:(?:te |le )?recomiendo (?:comprar|vender|mantener)|deber[ií]as? (?:comprar|vender)|"
    r"compra(?:r)? ya|es (?:una )?(?:buena )?compra|vende(?:r)? ya|"
    r"(?:you |we )?should (?:buy|sell|short)|i (?:would )?recommend (?:buying|selling)|"
    r"strong (?:buy|sell)|(?:is|looks like) a (?:clear )?(?:buy|sell))\b",
    re.IGNORECASE,
)
_PREDICTION = re.compile(
    r"\b(?:el precio (?:llegar[aá]|subir[aá]|bajar[aá]|alcanzar[aá])|"
    r"precio objetivo|target price|price target|"
    r"(?:will|is going to) (?:rise|fall|reach|hit|double))\b",
    re.IGNORECASE,
)
_TARGET = re.compile(r"\b(?:precio objetivo|target price|price target)\b", re.IGNORECASE)
_UNCERTAIN = re.compile(
    r"\b(?:podr[ií]a|posible|hip[oó]tesis|probable|si se confirma|condicional|"
    r"might|may|could|possibly|hypothesis|likely|if confirmed)\b",
    re.IGNORECASE,
)
#: Tools whose results are market evidence; playbooks, skills and memory are guidance only.
_EVIDENCE_TOOLS = frozenset({"analyze", "task", "preview_order"})
_NUDGE = HumanMessage(
    "Revisa tu respuesta: recomiendas o predices un precio sin análisis de este turno. "
    "Analiza el ticker o consulta a un analista y cita su evidencia, o exprésalo como una "
    "hipótesis condicional con su riesgo. Nunca des un precio objetivo."
)


class EvidenceGateMiddleware(AgentMiddleware[Any, Any]):
    """Give one corrective nudge before returning an unsupported recommendation."""

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelCallResult],
    ) -> ModelCallResult:
        result = handler(request)
        if _needs_nudge(request, result):
            answer = _last_answer(result)
            if answer is not None:
                return handler(request.override(messages=[*request.messages, answer, _NUDGE]))
        return result

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelCallResult]],
    ) -> ModelCallResult:
        result = await handler(request)
        if _needs_nudge(request, result):
            answer = _last_answer(result)
            if answer is not None:
                return await handler(request.override(messages=[*request.messages, answer, _NUDGE]))
        return result


def is_recommendation(text: str) -> bool:
    """Whether ``text`` recommends a trade or predicts a price."""
    return bool(_RECOMMENDATION.search(text) or _PREDICTION.search(text))


def _last_answer(result: ModelCallResult) -> AIMessage | None:
    if isinstance(result, AIMessage):
        return result
    response = result.model_response if isinstance(result, ExtendedModelResponse) else result
    return next(
        (message for message in reversed(response.result) if isinstance(message, AIMessage)),
        None,
    )


def _is_evidence(message: ToolMessage) -> bool:
    name = message.name or ""
    return name in _EVIDENCE_TOOLS or name.startswith(("compute_", "analyst_"))


def _needs_nudge(request: ModelRequest[Any], result: ModelCallResult) -> bool:
    answer = _last_answer(result)
    if answer is None or answer.tool_calls:
        return False
    text = answer.text
    if _TARGET.search(text):
        return True
    if not is_recommendation(text) or _UNCERTAIN.search(text):
        return False
    latest_user = max(
        (
            index
            for index, message in enumerate(request.messages)
            if isinstance(message, HumanMessage)
        ),
        default=-1,
    )
    return not any(
        isinstance(message, ToolMessage)
        and message.status != "error"
        and message.text.strip()
        and _is_evidence(message)
        for message in request.messages[latest_user + 1 :]
    )
