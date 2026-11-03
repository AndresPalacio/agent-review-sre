"""Host invocation resources stay live while workflow payloads stay isolated."""

import asyncio
import threading
from dataclasses import FrozenInstanceError, replace

import pytest
from pydantic import BaseModel

import bm_workflow as w


class Input(BaseModel):
    number: int


class Answer(BaseModel):
    answer: int


class Output(BaseModel):
    result: Answer


def compiled(node):
    return w.compile_workflow(
        w.workflow(name="invocations", version="1", input=Input, output=Output).flow(node)
    )


@pytest.mark.asyncio
async def test_live_resources_preserve_identity_and_config_snapshot():
    class Client:
        def __deepcopy__(self, memo):
            raise AssertionError("live client must not be copied")

        def __repr__(self):
            raise AssertionError("live client must not enter manifests")

    client = Client()
    lock = threading.Lock()
    host_context = {"client": client, "lock": lock}
    configuration = {"configurable": {"client": client}, "recursion_limit": 12}
    invocation = w.AgentInvocation(config=configuration, context=host_context)
    configuration["recursion_limit"] = 99
    with pytest.raises(FrozenInstanceError):
        invocation.context = None
    with pytest.raises(TypeError):
        invocation.config["recursion_limit"] = 99

    class Agent:
        async def ainvoke(self, payload, *, config, context):
            assert config["recursion_limit"] == 12
            assert config["configurable"]["client"] is client
            assert context is host_context
            assert context["lock"] is lock
            payload["number"] = 7
            config["recursion_limit"] = 200
            return {"structured_response": {"answer": payload["number"]}}

    node = w.agent_step(
        "agent",
        Agent(),
        output_schema=Answer,
        output_key="result",
        invocation_factory=lambda state, context: invocation,
    )
    payload = {"number": 1}
    assert await node.handler(payload) == {"result": {"answer": 7}}
    assert payload == {"number": 1}
    assert invocation.config["recursion_limit"] == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_kind", ["async", "sync", "callable"])
@pytest.mark.parametrize("async_factory", [False, True])
async def test_provider_and_factory_forms_receive_run_context(provider_kind, async_factory):
    loop_thread = threading.get_ident()
    contexts = []

    def factory(state, context):
        if not async_factory:
            assert threading.get_ident() != loop_thread
        contexts.append(context)
        assert state == {"number": 3}
        state["number"] = 100
        return w.AgentInvocation(config={"tags": ["run"]}, context=context)

    async def afactory(state, context):
        assert threading.get_ident() == loop_thread
        return factory(state, context)

    def respond(payload, *, config, context):
        assert context is contexts[0]
        assert context.cancellation is contexts[0].cancellation
        assert payload == {"number": 3}
        assert config == {"tags": ["run"]}
        return {"structured_response": {"answer": 4}}

    class AsyncAgent:
        async def ainvoke(self, payload, **kwargs):
            return respond(payload, **kwargs)

    class SyncAgent:
        def invoke(self, payload, **kwargs):
            assert threading.get_ident() != loop_thread
            return respond(payload, **kwargs)

    provider = {"async": AsyncAgent(), "sync": SyncAgent(), "callable": respond}[provider_kind]
    graph = compiled(
        w.agent_step(
            "agent",
            provider,
            output_schema=Answer,
            output_key="result",
            invocation_factory=afactory if async_factory else factory,
        )
    )
    context = w.RunContext("invocations", "1", graph.definition_digest)
    result = await w.arun_workflow(graph, {"number": 3}, context=context)
    assert result.status is w.RunStatus.COMPLETE
    assert result.output == Output(result=Answer(answer=4))
    assert contexts == [context]
    assert contexts[0] is context


@pytest.mark.asyncio
async def test_concurrent_invocations_do_not_share_config_or_context():
    ready = asyncio.Event()
    entered = []

    class Agent:
        async def ainvoke(self, payload, *, config, context):
            entered.append(context)
            if len(entered) == 2:
                ready.set()
            await ready.wait()
            assert config["configurable"]["number"] == payload["number"]
            assert context.run_id == str(payload["number"])
            return {"structured_response": {"answer": payload["number"]}}

    node = w.agent_step(
        "agent",
        Agent(),
        output_schema=Answer,
        output_key="result",
        invocation_factory=lambda state, context: w.AgentInvocation(
            config={"configurable": {"number": state["number"]}}, context=context
        ),
    )
    graph = compiled(node)
    results = await asyncio.gather(
        *(
            w.arun_workflow(
                graph,
                {"number": number},
                context=w.RunContext(
                    "invocations", "1", graph.definition_digest, run_id=str(number)
                ),
            )
            for number in (1, 2)
        )
    )
    assert [result.output.result.answer for result in results] == [1, 2]
    assert all(result.status is w.RunStatus.COMPLETE for result in results)
    assert entered[0] is not entered[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_factory", [False, True])
async def test_cancellation_reaches_factory_and_provider(cancel_factory):
    entered = asyncio.Event()
    stopped = asyncio.Event()
    seen_contexts = []

    async def wait(context):
        seen_contexts.append(context)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def factory(state, context):
        if cancel_factory:
            await wait(context)
        return w.AgentInvocation(context=context)

    class Agent:
        async def ainvoke(self, payload, *, config, context):
            await wait(context)

    graph = compiled(
        w.agent_step(
            "agent", Agent(), output_schema=Answer, output_key="result", invocation_factory=factory
        )
    )
    context = w.RunContext("invocations", "1", graph.definition_digest)
    task = asyncio.create_task(w.arun_workflow(graph, {"number": 1}, context=context))
    await asyncio.wait_for(entered.wait(), 2)
    context.cancellation.set()
    result = await asyncio.wait_for(task, 2)
    assert result.status is w.RunStatus.CANCELLED
    assert stopped.is_set()
    assert seen_contexts[0] is context


@pytest.mark.parametrize("deep", [False, True])
def test_dry_run_skips_factories_and_providers(deep):
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run invoked host code")

    kwargs = dict(output_schema=Answer, output_key="result", invocation_factory=forbidden)
    node = (
        w.deep_agent_step("agent", factory=forbidden, **kwargs)
        if deep
        else w.agent_step("agent", forbidden, **kwargs)
    )
    report = w.dry_run(compiled(node), {"number": 1}, fixtures={"agent": {"result": {"answer": 9}}})
    assert report.result.status is w.RunStatus.COMPLETE
    assert report.result.output == Output(result=Answer(answer=9))


@pytest.mark.asyncio
async def test_deep_agent_keeps_lazy_per_invocation_construction_with_async_factory():
    constructions = []
    invocation_calls = []

    async def create(**kwargs):
        assert kwargs == {"response_format": Answer}
        constructions.append(object())

        async def agent(payload, *, config):
            return {"structured_response": {"answer": config["answer"]}}

        return agent

    async def invocation(state, context):
        assert context is None
        invocation_calls.append(state["number"])
        return w.AgentInvocation(config={"answer": state["number"]})

    node = w.deep_agent_step(
        "agent",
        factory=create,
        output_schema=Answer,
        output_key="result",
        invocation_factory=invocation,
    )
    assert constructions == []
    assert await node.handler({"number": 1}) == {"result": {"answer": 1}}
    assert await node.handler({"number": 2}) == {"result": {"answer": 2}}
    assert len(constructions) == 2
    assert invocation_calls == [1, 2]


@pytest.mark.parametrize("deep", [False, True])
def test_manifest_changes_definition_digest_without_reading_live_provider(deep):
    class Provider:
        def __repr__(self):
            raise AssertionError("provider must not be represented")

    provider = Provider()
    revisions = {"model": "model-v1", "tools": "tools-v2"}
    manifest = w.AgentManifest(revisions=revisions)
    revisions["model"] = "changed-after-snapshot"

    def build(model_version):
        kwargs = dict(
            output_schema=Answer,
            output_key="result",
            manifest=model_version,
        )
        return compiled(
            w.deep_agent_step("agent", factory_kwargs={"model": provider}, **kwargs)
            if deep
            else w.agent_step("agent", provider, **kwargs)
        )

    first = build(manifest)
    same = build(w.AgentManifest(revisions={"tools": "tools-v2", "model": "model-v1"}))
    second = build(w.AgentManifest(revisions={"model": "model-v2", "tools": "tools-v2"}))
    assert first.definition_digest == same.definition_digest
    assert first.definition_digest != second.definition_digest


@pytest.mark.parametrize("revisions", [{"model": object()}, {1: "v1"}, {"": "v1"}, {"model": ""}])
def test_manifest_rejects_values_that_are_not_version_references(revisions):
    with pytest.raises((TypeError, ValueError)):
        w.AgentManifest(revisions=revisions)


@pytest.mark.asyncio
async def test_no_factory_preserves_single_argument_callable_behavior():
    def agent(payload):
        return {"structured_response": {"answer": payload["number"]}}

    node = w.agent_step("agent", agent, output_schema=Answer, output_key="result")
    assert await node.handler({"number": 8}) == {"result": {"answer": 8}}


@pytest.mark.asyncio
async def test_invalid_invocation_factory_result_fails_before_provider_call():
    def forbidden(payload):
        pytest.fail("invalid invocation reached provider")

    node = w.agent_step(
        "agent",
        forbidden,
        output_schema=Answer,
        output_key="result",
        invocation_factory=lambda state, context: {},
    )
    with pytest.raises(TypeError, match="AgentInvocation"):
        await node.handler({"number": 1})


@pytest.mark.asyncio
async def test_invocation_factory_runs_once_for_each_explicit_retry_attempt():
    factory_calls = []

    def invocation(state, context):
        factory_calls.append(context)
        return w.AgentInvocation(config={"attempt": len(factory_calls)})

    async def agent(payload, *, config):
        if config["attempt"] == 1:
            raise w.TransientError("retry explicitly authorized by host policy")
        return {"structured_response": {"answer": config["attempt"]}}

    node = w.agent_step(
        "agent",
        agent,
        output_schema=Answer,
        output_key="result",
        invocation_factory=invocation,
    )
    node = replace(node, policy=w.ExecutionPolicy(max_attempts=2, safe_to_retry=True))
    result = await w.arun_workflow(compiled(node), {"number": 1})
    assert result.status is w.RunStatus.COMPLETE
    assert result.output == Output(result=Answer(answer=2))
    assert factory_calls == [result.context, result.context]
