"""Hide infrastructure identifiers from the model and restore them in everything it returns."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import fields, is_dataclass, replace
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel

from fin_graph.safety.masking import Masker, MaskingPolicy

ModelCallResult = ModelResponse[Any] | AIMessage | ExtendedModelResponse[Any]


class MaskingMiddleware(AgentMiddleware[Any, Any]):
    """Mask identifiers in each model request; unmask the reply, its tool calls and its output.

    Agent state always holds real values: only what is sent to the model is
    masked, and the reply is unmasked before it is stored or any tool runs, so
    tools receive real identifiers. Disabled policies pass calls through.
    """

    def __init__(self, policy: MaskingPolicy) -> None:
        super().__init__()
        self._policy = policy

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelCallResult],
    ) -> ModelCallResult:
        if not self._policy.enabled:
            return handler(request)
        masker = Masker(self._policy)
        return _unmask_result(handler(_mask_request(request, masker)), masker)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelCallResult]],
    ) -> ModelCallResult:
        if not self._policy.enabled:
            return await handler(request)
        masker = Masker(self._policy)
        return _unmask_result(await handler(_mask_request(request, masker)), masker)


def _mask_request(request: ModelRequest[Any], masker: Masker) -> ModelRequest[Any]:
    messages = [_map_message(message, masker.mask_value) for message in request.messages]
    if request.system_message is None:
        return request.override(messages=messages)
    system_message = _map_message(request.system_message, masker.mask_value)
    return request.override(messages=messages, system_message=system_message)


def _unmask_result(result: ModelCallResult, masker: Masker) -> ModelCallResult:
    if isinstance(result, AIMessage):
        return _map_message(result, masker.unmask_value)
    if isinstance(result, ExtendedModelResponse):
        return replace(result, model_response=_unmask_response(result.model_response, masker))
    return _unmask_response(result, masker)


def _unmask_response(response: ModelResponse[Any], masker: Masker) -> ModelResponse[Any]:
    return ModelResponse(
        result=[_map_message(message, masker.unmask_value) for message in response.result],
        structured_response=_unmask_structured(response.structured_response, masker),
    )


def _map_message[MessageT: BaseMessage](
    message: MessageT, transform: Callable[[Any], Any]
) -> MessageT:
    update: dict[str, Any] = {"content": transform(message.content)}
    if isinstance(message, AIMessage) and message.tool_calls:
        update["tool_calls"] = [
            {**call, "args": transform(call["args"])} for call in message.tool_calls
        ]
    return message.model_copy(update=update)


def _unmask_structured(value: Any, masker: Masker) -> Any:
    if isinstance(value, BaseModel):
        return type(value).model_validate(masker.unmask_value(value.model_dump()))
    if is_dataclass(value) and not isinstance(value, type):
        changes = {
            item.name: _unmask_structured(getattr(value, item.name), masker)
            for item in fields(value)
        }
        return replace(value, **changes)
    return masker.unmask_value(value)
