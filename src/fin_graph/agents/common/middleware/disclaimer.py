"""Append the educational, paper-only disclaimer to chat answers about trading.

Any final answer that recommends, predicts, or talks about an order or a position gets the
disclaimer once. The text is added to the stored message itself, so every channel that shows
the conversation (CLI, web, Slack, Telegram) shows it too.
"""

from __future__ import annotations

import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from fin_graph.agents.common.middleware.evidence_gate import is_recommendation

DISCLAIMER = (
    "Aviso: análisis educativo sobre una cartera simulada (paper). "
    "No es asesoría financiera ni una recomendación personalizada."
)
_TRADING = re.compile(
    r"\b(?:orden(?:es)?|posici[oó]n(?:es)?|compra[rs]?|vend(?:e|er|es)|corto|"
    r"order|position|buy|sell|short|cover)\b",
    re.IGNORECASE,
)


def needs_disclaimer(text: str) -> bool:
    return DISCLAIMER not in text and (is_recommendation(text) or bool(_TRADING.search(text)))


class DisclaimerMiddleware(AgentMiddleware[Any, Any]):
    """Stamp the final answer of a turn when it is about trading."""

    def after_model(self, state: AgentState[Any], runtime: Runtime[Any]) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        if not messages:
            return None
        answer = messages[-1]
        if not isinstance(answer, AIMessage) or answer.tool_calls:
            return None
        if not needs_disclaimer(answer.text):
            return None
        if isinstance(answer.content, str):
            content: Any = f"{answer.content.rstrip()}\n\n_{DISCLAIMER}_"
        else:
            content = [*answer.content, {"type": "text", "text": f"\n\n_{DISCLAIMER}_"}]
        # Same id: the messages reducer replaces the answer instead of adding a second one.
        return {"messages": [answer.model_copy(update={"content": content})]}

    async def aafter_model(
        self, state: AgentState[Any], runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        return self.after_model(state, runtime)
