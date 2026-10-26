"""Routing contracts; the provider boundary uses the real SDK with local HTTP."""

import asyncio
import json
from collections.abc import Awaitable, Callable

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from fin_graph.work.policy import Decision, OpenAIDecisionPolicy, Route, RulesPolicy


@pytest.mark.parametrize(
    ("text", "route"),
    [
        ("Hola", Route.DIRECT),
        ("Hello!", Route.DIRECT),
        ("¿Qué es el P/E?", Route.DIRECT),
        ("Explain free cash flow", Route.DIRECT),
        ("Analiza las acciones de Apple", Route.ANALYZE),
        ("Analyze MSFT valuation", Route.ANALYZE),
        ("Revisa los fundamentales de KO", Route.ANALYZE),
        ("¿Cómo ves los técnicos de NVDA?", Route.ANALYZE),
        ("", Route.CLARIFY),
        ("   ", Route.CLARIFY),
        ("hazlo", Route.CLARIFY),
        ("Vende todo", Route.CLARIFY),
    ],
)
async def test_rules_route_without_claiming_model_inference(text: str, route: Route) -> None:
    decision = await RulesPolicy().decide(text)

    assert decision.route is route
    assert decision.policy == "rules"
    assert decision.model is None
    assert decision.confidence is None
    assert decision.fallback_reason is None


@pytest.mark.parametrize(
    ("text", "eligible", "route"),
    [
        ("Analiza DEMO", (Route.DIRECT, Route.CLARIFY), Route.CLARIFY),
        ("hola", (Route.ANALYZE, Route.CLARIFY), Route.CLARIFY),
        ("hazlo", (Route.ANALYZE, Route.DIRECT), Route.DIRECT),
        ("hola", (Route.ANALYZE,), Route.ANALYZE),
    ],
)
async def test_rules_never_choose_an_ineligible_route(
    text: str, eligible: tuple[Route, ...], route: Route
) -> None:
    assert (await RulesPolicy().decide(text, eligible=eligible)).route is route


async def test_rules_reject_an_empty_eligible_set() -> None:
    with pytest.raises(ValueError, match="eligible"):
        await RulesPolicy().decide("hola", eligible=())


def test_decision_is_an_immutable_serializable_routing_record() -> None:
    decision = Decision(route=Route.DIRECT, policy="rules")

    assert decision.model_dump(mode="json") == {
        "route": "direct",
        "policy": "rules",
        "model": None,
        "confidence": None,
        "fallback_reason": None,
    }
    with pytest.raises(ValidationError, match="frozen"):
        decision.route = Route.ANALYZE


def sdk_client(
    handler: Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]],
) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key="test-only-no-credentials",
        base_url="https://openai.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def provider_response(answer: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(
            {
                "model": "gpt-6-luna",
                "answers": [answer],
                "usage": {
                    "input_tokens": 30,
                    "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": 0},
                    "output_tokens": 0,
                    "output_tokens_details": {"reasoning_tokens": 0},
                    "total_tokens": 30,
                },
            }
        ),
        headers={"content-type": "application/json"},
    )


def choice_answer(choice: object = "analyze", confidence: object = 0.94) -> dict[str, object]:
    return {
        "type": "choice",
        "name": "route",
        "choice": choice,
        "confidence": confidence,
        "probabilities": [
            {"value": "direct", "probability": 0.03},
            {"value": "analyze", "probability": 0.94},
            {"value": "clarify", "probability": 0.03},
        ],
    }


async def test_openai_uses_real_sdk_serialization_and_preserves_observed_confidence() -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return provider_response(choice_answer())

    async with sdk_client(handle) as client:
        decision = await OpenAIDecisionPolicy(client=client).decide(
            "Analiza DEMO", eligible=(Route.ANALYZE, Route.CLARIFY)
        )

    assert decision.model_dump(mode="json") == {
        "route": "analyze",
        "policy": "openai_decisions",
        "model": "gpt-6-luna",
        "confidence": 0.94,
        "fallback_reason": None,
    }
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url == "https://openai.invalid/v1/decisions"
    payload = json.loads(request.content)
    assert payload["input"] == "Analiza DEMO"
    assert payload["model"] == "gpt-6-luna"
    assert set(payload) == {"input", "model", "questions"}
    assert len(payload["questions"]) == 1
    question = payload["questions"][0]
    assert question["type"] == "choice"
    assert question["name"] == "route"
    assert question["instructions"]
    assert [choice["value"] for choice in question["choices"]] == ["analyze", "clarify"]
    assert all(choice["description"] for choice in question["choices"])
    assert request.extensions["timeout"]["read"] == 10


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_openai_requires_explicit_credentials_without_injected_client(
    monkeypatch: pytest.MonkeyPatch, api_key: str | None
) -> None:
    if api_key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", api_key)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIDecisionPolicy()


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ({"type": "refusal", "name": "route"}, "refusal"),
        (choice_answer(confidence=0.69), "low_confidence"),
        (choice_answer(choice="restart-service"), "invalid_choice"),
        (choice_answer(choice=True), "invalid_choice"),
        (choice_answer(confidence=-0.01), "invalid_confidence"),
        (choice_answer(confidence=1.01), "invalid_confidence"),
        (choice_answer(confidence=None), "invalid_confidence"),
        (choice_answer(confidence="uncertain"), "invalid_confidence"),
        (choice_answer(confidence=True), "invalid_confidence"),
        (choice_answer(confidence="0.99"), "invalid_confidence"),
        (choice_answer(confidence=float("nan")), "invalid_confidence"),
        (choice_answer(confidence=float("inf")), "invalid_confidence"),
        ({"type": "predicate", "name": "route", "probability": 0.99}, "invalid_response"),
        ({**choice_answer(), "name": "other_question"}, "invalid_response"),
    ],
)
async def test_provider_failures_use_rules_without_fabricating_model_confidence(
    answer: dict[str, object], reason: str
) -> None:
    async with sdk_client(lambda _: provider_response(answer)) as client:
        decision = await OpenAIDecisionPolicy(client).decide("Hola")

    assert decision.model_dump(mode="json") == {
        "route": "direct",
        "policy": "rules",
        "model": None,
        "confidence": None,
        "fallback_reason": reason,
    }


async def test_ineligible_provider_choice_falls_back_to_an_eligible_route() -> None:
    async with sdk_client(lambda _: provider_response(choice_answer())) as client:
        decision = await OpenAIDecisionPolicy(client).decide(
            "Analiza DEMO", eligible=(Route.DIRECT, Route.CLARIFY)
        )

    assert decision.route is Route.CLARIFY
    assert decision.fallback_reason == "ineligible_choice"
    assert decision.policy == "rules"


async def test_confidence_threshold_is_configurable_and_inclusive() -> None:
    async with sdk_client(lambda _: provider_response(choice_answer(confidence=0.8))) as client:
        accepted = await OpenAIDecisionPolicy(client, minimum_confidence=0.8).decide("Hola")
        rejected = await OpenAIDecisionPolicy(client, minimum_confidence=0.81).decide("Hola")

    assert accepted.route is Route.ANALYZE
    assert accepted.confidence == 0.8
    assert rejected.route is Route.DIRECT
    assert rejected.fallback_reason == "low_confidence"


async def test_empty_eligible_set_is_rejected_before_http() -> None:
    def unexpected_http(_: httpx.Request) -> httpx.Response:
        pytest.fail("empty eligible routes must not reach the provider")

    async with sdk_client(unexpected_http) as client:
        with pytest.raises(ValueError, match="eligible"):
            await OpenAIDecisionPolicy(client).decide("Hola", eligible=())


async def test_single_eligible_route_needs_no_invalid_one_choice_api_call() -> None:
    def unexpected_http(_: httpx.Request) -> httpx.Response:
        pytest.fail("Decisions requires at least two distinct choices")

    async with sdk_client(unexpected_http) as client:
        decision = await OpenAIDecisionPolicy(client).decide(
            "Hola", eligible=(Route.ANALYZE, Route.ANALYZE)
        )

    assert decision.route is Route.ANALYZE
    assert decision.policy == "rules"
    assert decision.model is None
    assert decision.confidence is None
    assert decision.fallback_reason == "single_eligible_route"


async def test_http_timeout_uses_eligible_fallback_without_leaking_exception_content() -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("secret-provider-body", request=request)

    async with sdk_client(handle) as client:
        decision = await OpenAIDecisionPolicy(client).decide(
            "Analiza DEMO", eligible=(Route.DIRECT, Route.CLARIFY)
        )

    assert len(requests) == 1
    assert decision.route is Route.CLARIFY
    assert decision.fallback_reason == "timeout"
    assert "secret-provider-body" not in decision.model_dump_json()


async def test_total_timeout_bounds_slow_transport() -> None:
    async def handle(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return provider_response(choice_answer())

    async with sdk_client(handle) as client:
        decision = await OpenAIDecisionPolicy(client, timeout_seconds=0.01).decide("Hola")

    assert decision.route is Route.DIRECT
    assert decision.fallback_reason == "timeout"


async def test_provider_error_is_sanitized_and_does_not_retry() -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, json={"error": {"message": "secret-provider-body"}})

    async with sdk_client(handle) as client:
        decision = await OpenAIDecisionPolicy(client).decide("Hola")

    assert len(requests) == 1
    assert decision.route is Route.DIRECT
    assert decision.fallback_reason == "provider_error"
    assert "secret-provider-body" not in decision.model_dump_json()


@pytest.mark.parametrize("body", ["not-json", '{"answers":[]}', '{"answers":null}', "[]"])
async def test_malformed_response_uses_rules(body: str) -> None:
    async with sdk_client(
        lambda _: httpx.Response(200, content=body, headers={"content-type": "application/json"})
    ) as client:
        decision = await OpenAIDecisionPolicy(client).decide("Hola")

    assert decision.route is Route.DIRECT
    assert decision.fallback_reason == "invalid_response"


async def test_caller_cancellation_is_not_converted_to_a_routing_decision() -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    async with sdk_client(handle) as client:
        with pytest.raises(asyncio.CancelledError):
            await OpenAIDecisionPolicy(client).decide("Hola")


@pytest.mark.parametrize("minimum_confidence", [-0.1, 1.1, float("nan"), float("inf"), True])
async def test_invalid_threshold_is_a_configuration_error(minimum_confidence: float) -> None:
    async with sdk_client(lambda _: provider_response(choice_answer())) as client:
        with pytest.raises(ValueError, match="minimum_confidence"):
            OpenAIDecisionPolicy(client, minimum_confidence=minimum_confidence)


@pytest.mark.parametrize("timeout_seconds", [0, -1, float("nan"), float("inf"), True])
async def test_invalid_timeout_is_a_configuration_error(timeout_seconds: float) -> None:
    async with sdk_client(lambda _: provider_response(choice_answer())) as client:
        with pytest.raises(ValueError, match="timeout_seconds"):
            OpenAIDecisionPolicy(client, timeout_seconds=timeout_seconds)
