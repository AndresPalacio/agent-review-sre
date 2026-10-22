"""Interchangeable routing policies for the opt-in asynchronous work pilot."""

import asyncio
import json
import math
import os
import re
from enum import StrEnum
from typing import Protocol

from openai import APIError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ConfigDict


class Route(StrEnum):
    DIRECT = "direct"
    INVESTIGATE = "investigate"
    CLARIFY = "clarify"


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: Route
    policy: str
    model: str | None = None
    confidence: float | None = None
    fallback_reason: str | None = None


class DecisionPolicy(Protocol):
    async def decide(
        self,
        text: str,
        *,
        eligible: tuple[Route, ...] = (Route.DIRECT, Route.INVESTIGATE, Route.CLARIFY),
    ) -> Decision: ...


class RulesPolicy:
    """Conservative deterministic rules: no LLM inference or model confidence."""

    async def decide(
        self,
        text: str,
        *,
        eligible: tuple[Route, ...] = (Route.DIRECT, Route.INVESTIGATE, Route.CLARIFY),
    ) -> Decision:
        if not eligible:
            raise ValueError("eligible must contain at least one route")

        normalized = " ".join(text.casefold().split())
        if re.fullmatch(r"(?:hola|hello|hi|gracias|thanks)[.!?]*", normalized) or re.match(
            r"[¿]?(?:qué es|que es|what is|explain|explica|define)\b", normalized
        ):
            route = Route.DIRECT
        elif re.search(
            r"\b(?:investiga|investigate|investigar|incident|incidente|"
            r"crashloopbackoff|timeout|timeouts|5\d\d)\b",
            normalized,
        ):
            route = Route.INVESTIGATE
        else:
            route = Route.CLARIFY

        if route not in eligible:
            # Prefer clarification, then a direct response, over launching work.
            route = next(
                candidate
                for candidate in (Route.CLARIFY, Route.DIRECT, Route.INVESTIGATE)
                if candidate in eligible
            )
        return Decision(route=route, policy="rules")


class OpenAIDecisionPolicy:
    """Optional Decisions routing; the pilot threshold is not calibrated.

    ``minimum_confidence=0.7`` is a starting point for evaluation, not an
    authorization boundary or a claim of measured accuracy.
    """

    def __init__(
        self,
        client: AsyncOpenAI | None = None,
        *,
        timeout_seconds: float = 10,
        minimum_confidence: float = 0.7,
    ) -> None:
        if type(minimum_confidence) not in (int, float) or not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be a finite number between 0 and 1")
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        if client is None:
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not api_key:
                raise ValueError(
                    "OpenAIDecisionPolicy requires OPENAI_API_KEY or an injected client"
                )
            client = AsyncOpenAI(api_key=api_key)
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._minimum_confidence = minimum_confidence

    async def decide(
        self,
        text: str,
        *,
        eligible: tuple[Route, ...] = (Route.DIRECT, Route.INVESTIGATE, Route.CLARIFY),
    ) -> Decision:
        if not eligible:
            raise ValueError("eligible must contain at least one route")
        eligible = tuple(dict.fromkeys(eligible))
        if len(eligible) == 1:
            return await self._fallback(text, eligible, "single_eligible_route")
        descriptions = {
            Route.DIRECT: "Answer a greeting or general conceptual question without investigating.",
            Route.INVESTIGATE: "Investigate an incident using read-only metrics and logs.",
            Route.CLARIFY: "Ask for missing context when the request is ambiguous or unsupported.",
        }
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._client.with_options(
                    max_retries=0
                ).decisions.with_raw_response.create(
                    model="gpt-6-luna",
                    input=text,
                    questions=[
                        {
                            "type": "choice",
                            "name": "route",
                            "instructions": "Select the appropriate route for this SRE request.",
                            "choices": [
                                {"value": route.value, "description": descriptions[route]}
                                for route in eligible
                            ],
                        }
                    ],
                    timeout=self._timeout_seconds,
                )
            # Inspect wire types before the SDK can coerce true or "0.9" to floats.
            payload = response.http_response.json()
        except (APITimeoutError, TimeoutError):
            return await self._fallback(text, eligible, "timeout")
        except APIError:
            return await self._fallback(text, eligible, "provider_error")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return await self._fallback(text, eligible, "invalid_response")

        if not isinstance(payload, dict):
            return await self._fallback(text, eligible, "invalid_response")
        answers = payload.get("answers")
        model = payload.get("model")
        if (
            not isinstance(answers, list)
            or len(answers) != 1
            or not isinstance(answers[0], dict)
            or answers[0].get("name") != "route"
            or not isinstance(model, str)
            or not model
        ):
            return await self._fallback(text, eligible, "invalid_response")
        answer = answers[0]
        if answer.get("type") == "refusal":
            return await self._fallback(text, eligible, "refusal")
        if answer.get("type") != "choice":
            return await self._fallback(text, eligible, "invalid_response")
        choice = answer.get("choice")
        if not isinstance(choice, str):
            return await self._fallback(text, eligible, "invalid_choice")
        try:
            route = Route(choice)
        except ValueError:
            return await self._fallback(text, eligible, "invalid_choice")
        if route not in eligible:
            return await self._fallback(text, eligible, "ineligible_choice")
        confidence = answer.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (float, int))
            or not 0 <= confidence <= 1
        ):
            return await self._fallback(text, eligible, "invalid_confidence")
        if confidence < self._minimum_confidence:
            return await self._fallback(text, eligible, "low_confidence")
        return Decision(
            route=route,
            policy="openai_decisions",
            model=model,
            confidence=confidence,
        )

    async def _fallback(self, text: str, eligible: tuple[Route, ...], reason: str) -> Decision:
        decision = await RulesPolicy().decide(text, eligible=eligible)
        return decision.model_copy(update={"fallback_reason": reason})
