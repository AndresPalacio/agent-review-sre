from __future__ import annotations

from typing import Literal

import pytest
from langchain_core.messages import AIMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field

from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.llm import structured
from fin_graph.llm.structured import extract_json, structured_call


class Signal(BaseModel):
    ticker: str
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0, le=100)


def _neutral(reason: str) -> Signal:
    return Signal(ticker="AAPL", signal="neutral", confidence=0)


_VALID = {"ticker": "AAPL", "signal": "bullish", "confidence": 72}


async def test_native_structured_output_is_used_first() -> None:
    model = ScriptedChatModel(responses=[tool_call_message("Signal", _VALID, "call-1")])

    result = await structured_call(model, Signal, "Score the ticker.", {"ticker": "AAPL"}, _neutral)

    assert result.value == Signal(**_VALID)
    assert result.source == "native"
    assert not result.degraded
    assert model.remaining == 0


async def test_plain_reply_with_json_recovers_when_native_output_fails() -> None:
    model = ScriptedChatModel(
        responses=[
            AIMessage(content="I think Apple looks good."),
            AIMessage(
                content='Here it is:\n```json\n{"ticker": "AAPL", "signal": "bullish", '
                '"confidence": 72}\n```'
            ),
        ]
    )

    result = await structured_call(model, Signal, "Score the ticker.", {"ticker": "AAPL"}, _neutral)

    assert result.value == Signal(**_VALID)
    assert result.source == "text"
    assert result.errors[0].startswith("native:")
    retry_system = model.calls[1][0].content
    assert "JSON Schema" in str(retry_system)
    assert '"confidence"' in str(retry_system)


async def test_fallback_receives_the_reason_and_is_marked_degraded() -> None:
    reasons: list[str] = []

    def fallback(reason: str) -> Signal:
        reasons.append(reason)
        return _neutral(reason)

    model = ScriptedChatModel(
        responses=[
            AIMessage(content="no idea"),
            AIMessage(content='{"ticker": "AAPL", "signal": "to the moon", "confidence": 900}'),
        ]
    )

    result = await structured_call(model, Signal, "Score the ticker.", "AAPL", fallback)

    assert result.value.signal == "neutral"
    assert result.degraded
    assert len(result.errors) == 2
    assert "native:" in reasons[0] and "text:" in reasons[0]


async def test_a_model_that_raises_still_ends_in_the_fallback() -> None:
    model = ScriptedChatModel(responses=[])

    result = await structured_call(model, Signal, "Score the ticker.", {}, _neutral)

    assert result.degraded
    assert all("ScriptedModelExhaustedError" in error for error in result.errors)


def test_ollama_models_use_the_json_schema_format() -> None:
    model = ChatOllama(model="llama3.2:3b")

    runnable = structured._native(model, Signal)

    bound = runnable.first  # type: ignore[attr-defined]
    assert bound.kwargs["format"] == Signal.model_json_schema()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('The answer is {"a": {"b": 2}} as requested.', {"a": {"b": 2}}),
        ('Note {"text": "a } inside", "n": 3} done', {"text": "a } inside", "n": 3}),
        ([{"type": "thinking", "thinking": "x"}, {"type": "text", "text": '{"a": 1}'}], {"a": 1}),
        ("no json here", None),
        ("[1, 2, 3]", None),
        (None, None),
    ],
)
def test_extract_json(content: object, expected: dict[str, object] | None) -> None:
    assert extract_json(content) == expected
