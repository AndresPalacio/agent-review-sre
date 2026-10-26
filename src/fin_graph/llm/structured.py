"""Structured output that still ends in a valid object when a small local model misbehaves.

Every LLM step of the analysis must finish even on a 3B model, so a call walks down three rungs:

1. native structured output (Ollama's ``json_schema`` format, tool calling elsewhere);
2. a plain completion asked for JSON, parsed with :func:`extract_json`;
3. the caller's deterministic ``fallback``, marked as degraded.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel

type StructuredSource = Literal["native", "text", "fallback"]

_JSON_ONLY = (
    "Answer with one JSON object that validates against this JSON Schema. "
    "No prose, no markdown, no keys outside the schema.\nSCHEMA:\n{schema}"
)


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    """The parsed value, which rung produced it, and why the earlier rungs failed."""

    value: T
    source: StructuredSource
    errors: tuple[str, ...] = ()

    @property
    def degraded(self) -> bool:
        """True when the value came from the deterministic fallback, not from the model."""
        return self.source == "fallback"


def extract_json(content: Any) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model reply; ``None`` when there is none.

    Handles content blocks, fenced ```json blocks, bare JSON and JSON surrounded by prose.
    Ported from ai-hedge-fund ``src/utils/llm.extract_json_from_response`` (MIT).
    """
    text = _text_of(content)
    for candidate in (*_fenced_blocks(text), text.strip(), _first_object(text)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block if isinstance(block, str) else str(block.get("text", ""))
            for block in content
            if isinstance(block, str) or (isinstance(block, dict) and block.get("type") == "text")
        ]
        return "\n".join(parts)
    return ""


def _fenced_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for fence in ("```json", "```"):
        start = text.find(fence)
        if start == -1:
            continue
        body = text[start + len(fence) :]
        end = body.find("```")
        if end != -1:
            blocks.append(body[:end].strip())
    return blocks


def _first_object(text: str) -> str | None:
    """The first brace-balanced ``{...}`` span, skipping braces inside JSON strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _payload_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _native(model: BaseChatModel, schema: type[BaseModel]) -> Any:
    if isinstance(model, ChatOllama):
        # Ollama constrains decoding to the schema, which is what keeps a 3B model on track.
        return model.with_structured_output(schema, method="json_schema")
    return model.with_structured_output(schema)


def _reason(error: Exception) -> str:
    return f"{type(error).__name__}: {str(error)[:300]}"


async def structured_call[T: BaseModel](
    model: BaseChatModel,
    schema: type[T],
    prompt: str,
    payload: Any,
    fallback: Callable[[str], T],
) -> StructuredResult[T]:
    """Ask ``model`` for a ``schema`` instance; never raises for model or parsing failures.

    ``fallback`` receives a short reason and must build the deterministic answer.
    Cancellation still propagates, so deadlines set by the caller keep working.
    """
    messages: list[BaseMessage] = [
        SystemMessage(content=prompt),
        HumanMessage(content=_payload_text(payload)),
    ]
    errors: list[str] = []
    try:
        native = await _native(model, schema).ainvoke(messages)
        return StructuredResult(schema.model_validate(native), "native")
    except Exception as error:
        errors.append(f"native: {_reason(error)}")

    schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    retry = [
        SystemMessage(content=f"{prompt}\n\n{_JSON_ONLY.format(schema=schema_text)}"),
        *messages[1:],
    ]
    try:
        reply = await model.ainvoke(retry)
        parsed = extract_json(reply.content)
        if parsed is None:
            raise ValueError("reply contains no JSON object")
        return StructuredResult(schema.model_validate(parsed), "text", tuple(errors))
    except Exception as error:
        errors.append(f"text: {_reason(error)}")

    reason = "; ".join(errors)
    return StructuredResult(fallback(reason), "fallback", tuple(errors))
