"""Editable reasoning middleware, installed in addition to the fixed safety stack."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse


class AnalysisDiscipline(AgentMiddleware[Any, Any]):
    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        # This implementation and its registration are both editable surfaces.
        return await handler(request)
