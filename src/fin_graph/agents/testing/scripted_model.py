"""Offline chat model for tests: replays scripted AI messages in call order."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr


class ScriptedModelExhaustedError(RuntimeError):
    """An agent asked for more responses than the script holds."""


class ScriptedChatModel(BaseChatModel):
    """Return ``responses`` one per call; bound tools never change the output.

    Agents sharing one instance consume the queue in call order. Give each
    parallel branch (for example each ``Send`` target) its own instance so the
    order stays deterministic.
    """

    responses: list[AIMessage]
    _queue: deque[AIMessage] = PrivateAttr(default_factory=deque)
    _calls: list[list[BaseMessage]] = PrivateAttr(default_factory=list)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    def model_post_init(self, context: Any, /) -> None:
        super().model_post_init(context)
        self._queue.extend(self.responses)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        with self._lock:
            self._calls.append(list(messages))
            if not self._queue:
                raise ScriptedModelExhaustedError("ScriptedChatModel ran out of responses")
            message = self._queue.popleft()
        return ChatResult(generations=[ChatGeneration(message=message.model_copy(deep=True))])

    @property
    def remaining(self) -> int:
        """How many scripted responses are still queued."""
        return len(self._queue)

    @property
    def calls(self) -> list[list[BaseMessage]]:
        """The message list each call received, in call order."""
        return self._calls


def tool_call_message(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    """An AI message that requests exactly one tool call."""
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}])
