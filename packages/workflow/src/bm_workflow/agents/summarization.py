"""Shared reservations around the native summary model, preserving SDK recovery."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.base import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import RunnableConfig
from pydantic import Field

from .contracts import SolverContext


class _BoundedSummaryModel(BaseChatModel):
    delegate: BaseChatModel = Field(exclude=True)
    context_getter: Callable[[], SolverContext] = Field(exclude=True)

    @property
    def _llm_type(self) -> str:
        return "bounded-summary-model"

    async def ainvoke(
        self,
        input: LanguageModelInput,
        config: RunnableConfig | None = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        # Keeping a BaseChatModel makes native summarization format its prompt
        # and recover from overflow. Every SDK retry re-enters this reservation.
        # Delegate invocation directly to avoid duplicate chat-model callbacks.
        context = self.context_getter()
        context.reserve("summary_calls")
        response = cast(
            AIMessage,
            await context.controlled(
                self.delegate.ainvoke(input, config=config, stop=stop, **kwargs)
            ),
        )
        context.record_tokens(response.usage_metadata)
        return response

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("Solver summaries require asynchronous invocation")


def bounded_summary_model(
    model: BaseChatModel, context_getter: Callable[[], SolverContext]
) -> BaseChatModel:
    """Keep native summary formatting/recovery with per-attempt shared limits."""
    return _BoundedSummaryModel(
        delegate=model, context_getter=context_getter, profile=model.profile
    )
