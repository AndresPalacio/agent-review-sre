"""Provider doubles check payloads; real adapters perform validation."""

import asyncio

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    number: int


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: int


@pytest.mark.asyncio
async def test_tool_maps_validates_and_offloads_sync_invocation():
    from bm_workflow.adapters import tool_step

    loop_thread = __import__("threading").get_ident()

    class Tool:
        def invoke(self, payload):
            assert __import__("threading").get_ident() != loop_thread
            assert payload == {"number": 4}
            return {"answer": payload["number"] * 2}

    node = tool_step(
        "tool",
        Tool(),
        input_schema=Query,
        output_schema=Answer,
        output_key="result",
        artifact_version="2",
        input_mapper=lambda state: {"number": state["value"]},
    )
    assert await node.handler({"value": 4}) == {"result": {"answer": 8}}
    assert node.kind == "external"
    with pytest.raises(ValidationError):
        await node.handler({"value": "4"})


@pytest.mark.asyncio
async def test_llm_uses_structured_output_and_revalidates_constructed_models():
    from bm_workflow.adapters import llm_step

    class Structured:
        async def ainvoke(self, prompt):
            assert prompt == [{"role": "user", "content": "calculate 3"}]
            return Answer.model_construct(answer="invalid")

    class Model:
        def with_structured_output(self, schema):
            assert schema is Answer
            return Structured()

    node = llm_step(
        "llm",
        Model(),
        prompt=lambda state: [{"role": "user", "content": f"calculate {state['value']}"}],
        output_schema=Answer,
        output_key="result",
        artifact_version="1",
    )
    with pytest.raises(ValidationError):
        await node.handler({"value": 3})


@pytest.mark.asyncio
async def test_agent_reads_structured_response_from_real_langgraph():
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    from bm_workflow.adapters import agent_step

    class State(TypedDict):
        number: int
        structured_response: dict

    graph = StateGraph(State)
    graph.add_node("answer", lambda state: {"structured_response": {"answer": state["number"] + 1}})
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)
    node = agent_step(
        "agent",
        graph.compile(),
        input_mapper=lambda s: {"number": s["value"]},
        output_schema=Answer,
        output_key="result",
        artifact_version="1",
    )
    assert await node.handler({"value": 4}) == {"result": {"answer": 5}}


@pytest.mark.asyncio
async def test_agent_rejects_missing_structured_response():
    from bm_workflow.adapters import agent_step

    class Agent:
        async def ainvoke(self, payload):
            return {"messages": []}

    node = agent_step("agent", Agent(), output_schema=Answer, output_key="result")
    with pytest.raises(ValueError, match="structured_response"):
        await node.handler({})


@pytest.mark.asyncio
async def test_deep_agent_builds_lazily_with_injected_factory():
    from bm_workflow.adapters import deep_agent_step

    constructions = []

    class Agent:
        async def ainvoke(self, payload):
            assert payload == {"number": 5}
            return {"structured_response": {"answer": 10}}

    def factory(**kwargs):
        assert kwargs == {"model": "host-model", "response_format": Answer}
        constructions.append(True)
        return Agent()

    node = deep_agent_step(
        "deep",
        factory=factory,
        factory_kwargs={"model": "host-model"},
        output_schema=Answer,
        output_key="result",
    )
    assert not constructions
    assert await node.handler({"number": 5}) == {"result": {"answer": 10}}
    assert constructions == [True]


@pytest.mark.asyncio
async def test_tool_cancellation_propagates_to_async_provider():
    from bm_workflow.adapters import tool_step

    started = asyncio.Event()
    cancelled = asyncio.Event()

    class Tool:
        async def ainvoke(self, payload):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    node = tool_step("tool", Tool(), input_schema=Query, output_schema=Answer, output_key="result")
    task = asyncio.create_task(node.handler({"number": 1}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_tool_revalidates_nested_constructed_models_and_extras():
    from bm_workflow.adapters import tool_step

    class Envelope(BaseModel):
        answer: Answer

    node = tool_step(
        "tool",
        lambda _: Envelope.model_construct(answer=Answer.model_construct(answer="bad")),
        input_schema=Query,
        output_schema=Envelope,
        output_key="result",
    )
    with pytest.raises(ValidationError):
        await node.handler({"number": 1})


@pytest.mark.asyncio
async def test_deep_agent_preserves_injected_provider_identity():
    from bm_workflow.adapters import deep_agent_step

    class HostModel:
        def __deepcopy__(self, memo):
            raise TypeError("provider owns a noncopyable client")

    model = HostModel()

    class Agent:
        async def ainvoke(self, payload):
            return {"structured_response": {"answer": 1}}

    def factory(**kwargs):
        assert kwargs["model"] is model
        return Agent()

    node = deep_agent_step(
        "deep",
        factory=factory,
        factory_kwargs={"model": model},
        output_schema=Answer,
        output_key="result",
    )
    assert await node.handler({}) == {"result": {"answer": 1}}


def test_tool_dry_run_intercepts_provider_and_checks_fixture_contract():
    import bm_workflow as w
    from bm_workflow.adapters import tool_step

    class Output(BaseModel):
        result: dict

    def forbidden(_):
        pytest.fail("dry run must not invoke the provider")

    node = tool_step(
        "remote", forbidden, input_schema=Query, output_schema=Answer, output_key="result"
    )
    compiled = w.compile_workflow(
        w.workflow(name="tool-dry", version="1", input=Query, output=Output).flow(node)
    )
    report = w.dry_run(compiled, {"number": 1}, fixtures={"remote": {"result": {"answer": 8}}})
    assert report.result.output == Output(result={"answer": 8})
    invalid = w.dry_run(compiled, {"number": 1}, fixtures={"remote": {"result": {"answer": "bad"}}})
    assert invalid.result.status is w.RunStatus.FAILED


@pytest.mark.asyncio
async def test_tool_accepts_canonical_state_with_aliased_contract_fields():
    from bm_workflow.adapters import tool_step
    from bm_workflow.runtime import execute_step

    class AliasedQuery(BaseModel):
        number: int = Field(alias="input_number")

    class AliasedAnswer(BaseModel):
        answer: int = Field(alias="provider_answer")

    def provider(payload):
        assert payload == {"number": 4}
        return AliasedAnswer(provider_answer=8)

    node = tool_step(
        "aliased",
        provider,
        input_schema=AliasedQuery,
        output_schema=AliasedAnswer,
        output_key="result",
    )
    assert await execute_step(node, {"number": 4}) == {"number": 4, "result": {"answer": 8}}


@pytest.mark.asyncio
async def test_tool_revalidates_constructed_models_nested_inside_provider_mapping():
    from bm_workflow.adapters import tool_step

    class Envelope(BaseModel):
        answer: Answer

    node = tool_step(
        "nested",
        lambda _: {"answer": Answer.model_construct(answer="invalid")},
        input_schema=Query,
        output_schema=Envelope,
        output_key="result",
    )
    with pytest.raises(ValidationError):
        await node.handler({"number": 1})
