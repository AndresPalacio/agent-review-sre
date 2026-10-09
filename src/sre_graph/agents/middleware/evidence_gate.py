"""Ask the chat model to reconsider an unsupported root-cause assertion once."""

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

_ROOT_CAUSE = re.compile(
    r"\b(?:la causa ra[ií]z (?:es|fue)|"
    r"(?:the )?root cause (?:is|was)|"
    r"se debi[oó] a|fue causad[oa] por|"
    r"(?:was|is) caused by)\b",
    re.IGNORECASE,
)
_UNCERTAIN = re.compile(
    r"\b(?:podr[ií]a|posible|hip[oó]tesis|probable|"
    r"might|may|could|possibly|hypothesis|likely)\b",
    re.IGNORECASE,
)
_NUDGE = HumanMessage(
    "Revisa tu afirmación de causa raíz: no hay resultados de investigación en este turno. "
    "Si el usuario aportó evidencia, cítala; de lo contrario, consulta evidencia antes "
    "de atribuir una causa o exprésala como hipótesis con incertidumbre."
)


class EvidenceGateMiddleware(AgentMiddleware[Any, Any]):
    """Give one corrective nudge before returning an unsupported RCA claim."""

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


def _last_answer(result: ModelCallResult) -> AIMessage | None:
    if isinstance(result, AIMessage):
        return result
    response = result.model_response if isinstance(result, ExtendedModelResponse) else result
    return next(
        (message for message in reversed(response.result) if isinstance(message, AIMessage)),
        None,
    )


def _needs_nudge(request: ModelRequest[Any], result: ModelCallResult) -> bool:
    answer = _last_answer(result)
    if answer is None or answer.tool_calls:
        return False
    text = answer.text
    if not _ROOT_CAUSE.search(text) or _UNCERTAIN.search(text):
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
        and (message.name == "investigate" or (message.name or "").startswith("ask_"))
        for message in request.messages[latest_user + 1 :]
    )
