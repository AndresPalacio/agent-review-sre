import asyncio
import threading

import pytest
from pydantic import BaseModel, Field


class Number(BaseModel):
    value: int = Field(ge=0)


def api():
    import bm_workflow

    return bm_workflow


def increment(value: Number) -> Number:
    return Number(value=value.value + 1)


def compiled():
    w = api()
    return w.compile_workflow(
        w.workflow(name="count", version="1", input=Number, output=Number).flow(
            w.typed_step(
                "first", increment, input_schema=Number, output_schema=Number, artifact_version="1"
            ),
            w.typed_step(
                "second", increment, input_schema=Number, output_schema=Number, artifact_version="1"
            ),
        )
    )


def test_documented_typed_workflow_runs_on_langgraph():
    w = api()
    graph = compiled()
    assert graph.graph.get_graph().nodes.keys() >= {"first", "second"}
    result = w.WorkflowRegistry([graph]).run(name="count", version="1", input_data={"value": 1})
    assert result.status is w.RunStatus.COMPLETE
    assert result.output == Number(value=3)


def test_invalid_constructed_input_is_revalidated_before_handler():
    w = api()
    with pytest.raises(w.InputValidationError):
        w.run_workflow(compiled(), Number.model_construct(value=-1))


def test_raw_mutations_do_not_leak_and_output_is_projected():
    w = api()
    source = {"value": 3}

    def mutate(data):
        data["value"] = 999
        return {"ignored": True}

    definition = w.workflow(name="isolation", version="1", input=Number, output=Number).flow(
        w.step("mutate", mutate)
    )
    assert w.run_workflow(w.compile_workflow(definition), source).output == Number(value=3)
    assert source == {"value": 3}


def test_duplicate_ids_and_nonterminal_routes_are_rejected():
    w = api()
    builder = w.workflow(name="bad", version="1", input=Number, output=Number)
    with pytest.raises(w.DefinitionError):
        w.compile_workflow(builder.flow(w.step("same", dict), w.step("same", dict)))
    with pytest.raises(w.DefinitionError):
        builder.flow(
            w.route("route", lambda d: "yes", {"yes": [w.step("yes", dict)]}), w.step("after", dict)
        )


def test_route_executes_only_selected_branch_and_rejects_unknown_label():
    w = api()
    definition = w.workflow(name="routing", version="1", input=Number, output=Number).flow(
        w.route(
            "choose",
            lambda d: "yes" if d["value"] else "unknown",
            {
                "yes": [w.step("selected", lambda d: {"value": 42})],
                "no": [w.step("unselected", lambda d: {"value": 99})],
            },
        )
    )
    graph = w.compile_workflow(definition)
    assert w.run_workflow(graph, {"value": 1}).output.value == 42
    failed = w.run_workflow(graph, {"value": 0})
    assert failed.status is w.RunStatus.FAILED
    assert failed.cause.code == "invalid_route"


def test_registry_digest_versions_and_release_history():
    w = api()
    first = compiled()
    assert first.definition_digest == compiled().definition_digest
    changed = w.compile_workflow(
        w.workflow(name="count", version="1", input=Number, output=Number).flow(
            w.step("changed", dict, artifact_version="2")
        )
    )
    registry = w.WorkflowRegistry([first])
    with pytest.raises(w.DefinitionError):
        w.WorkflowRegistry([first, first])
    with pytest.raises(w.WorkflowNotFoundError):
        registry.resolve("count", "latest")
    with pytest.raises(w.DefinitionError):
        w.WorkflowRegistry([changed]).release_manifest(previous=registry.manifest())


async def test_async_retry_is_explicit_safe_and_bounded():
    w = api()
    calls = 0

    async def transient(data):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise w.TransientError("private upstream message")
        return {"value": 7}

    with pytest.raises(ValueError):
        w.ExecutionPolicy(max_attempts=2)
    node = w.step("retry", transient, policy=w.ExecutionPolicy(max_attempts=3, safe_to_retry=True))
    graph = w.compile_workflow(
        w.workflow(name="retry", version="1", input=Number, output=Number).flow(node)
    )
    result = await w.arun_workflow(graph, {"value": 0})
    assert result.output.value == 7
    assert calls == 3


async def test_timeout_is_not_retried_and_errors_are_sanitized():
    w = api()
    calls = 0

    async def blocked(data):
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()

    node = w.step(
        "blocked",
        blocked,
        policy=w.ExecutionPolicy(max_attempts=3, safe_to_retry=True, timeout_seconds=0.01),
    )
    graph = w.compile_workflow(
        w.workflow(name="timeout", version="1", input=Number, output=Number).flow(node)
    )
    result = await w.arun_workflow(graph, {"value": 1})
    assert result.status is w.RunStatus.TIMED_OUT
    assert calls == 1
    bad = w.compile_workflow(
        w.workflow(name="failed", version="1", input=Number, output=Number).flow(
            w.step("bad", lambda d: (_ for _ in ()).throw(RuntimeError("SECRET_API_KEY")))
        )
    )
    result = await w.arun_workflow(bad, {"value": 1})
    assert result.cause.code == "node_failed"
    assert "SECRET" not in repr(result)


async def test_cancellation_and_sync_entry_in_running_loop():
    w = api()
    graph = compiled()
    event = threading.Event()
    event.set()
    context = w.RunContext(
        name="count", version="1", definition_digest=graph.definition_digest, cancellation=event
    )
    assert (
        await w.arun_workflow(graph, {"value": 1}, context=context)
    ).status is w.RunStatus.CANCELLED
    with pytest.raises(RuntimeError, match="arun"):
        w.run_workflow(graph, {"value": 1})


def test_dry_run_intercepts_external_boundaries_and_blocks_missing_fixtures():
    w = api()

    def external(data):
        raise AssertionError("must not execute")

    graph = w.compile_workflow(
        w.workflow(name="dry", version="1", input=Number, output=Number).flow(
            w.step("remote", external, kind="external", output_schema=Number)
        )
    )
    report = w.dry_run(graph, {"value": 1}, fixtures={"remote": {"value": 8}})
    assert report.result.output.value == 8
    assert report.events[0].node_id == "remote"
    assert w.dry_run(graph, {"value": 1}, fixtures={}).result.cause.code == "missing_fixture"
    assert (
        w.dry_run(graph, {"value": 1}, fixtures={"remote": {"value": -1}}).result.status
        is w.RunStatus.FAILED
    )


def test_nested_constructed_models_are_rejected_before_any_handler_runs():
    w = api()

    class Envelope(BaseModel):
        children: list[Number]

    calls = []
    graph = w.compile_workflow(
        w.workflow(name="nested", version="1", input=Envelope, output=Envelope).flow(
            w.step("record", lambda data: calls.append(data) or {})
        )
    )
    with pytest.raises(w.InputValidationError):
        w.run_workflow(graph, {"children": [Number.model_construct(value=-5)]})
    assert calls == []


def test_alias_contracts_work_through_typed_steps_and_final_output():
    w = api()

    class Aliased(BaseModel):
        value: int = Field(alias="Value", serialization_alias="serialized")

    graph = w.compile_workflow(
        w.workflow(name="aliases", version="1", input=Aliased, output=Aliased).flow(
            w.typed_step(
                "identity",
                lambda data: data,
                input_schema=Aliased,
                output_schema=Aliased,
                artifact_version="1",
            )
        )
    )
    result = w.run_workflow(graph, {"Value": 7})
    assert result.status is w.RunStatus.COMPLETE
    assert result.output.value == 7


async def test_spontaneous_node_cancellation_has_consistent_status():
    w = api()

    async def cancelled(data):
        raise asyncio.CancelledError

    graph = w.compile_workflow(
        w.workflow(name="cancel", version="1", input=Number, output=Number).flow(
            w.step("cancel", cancelled)
        )
    )
    result = await w.arun_workflow(graph, {"value": 1})
    assert result.status is w.RunStatus.CANCELLED
    assert result.cause.node_id == "cancel"


async def test_caller_cancellation_propagates_and_drains_handler():
    w = api()
    started = asyncio.Event()
    drained = asyncio.Event()

    async def blocked(data):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            drained.set()

    graph = w.compile_workflow(
        w.workflow(name="drain", version="1", input=Number, output=Number).flow(
            w.step("wait", blocked)
        )
    )
    task = asyncio.create_task(w.arun_workflow(graph, {"value": 1}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert drained.is_set()


async def test_node_timeout_is_checked_when_late_result_finishes():
    from unittest.mock import patch

    w = api()
    clock = 0.0

    async def late(data):
        nonlocal clock
        clock = 10.0
        return data

    graph = w.compile_workflow(
        w.workflow(name="late", version="1", input=Number, output=Number).flow(
            w.step("late", late, policy=w.ExecutionPolicy(timeout_seconds=1))
        )
    )
    with patch("bm_workflow.runtime.time.monotonic", side_effect=lambda: clock):
        result = await w.arun_workflow(graph, {"value": 1})
    assert result.status is w.RunStatus.TIMED_OUT


@pytest.mark.parametrize("mode", ["direct", "persisted", "subflow", "tool"])
def test_json_contract_preserves_round_trip_through_steps_and_persistence(mode):
    from pydantic import Json

    w = api()

    class Document(BaseModel):
        value: Json[list[int]]

    graph = w.compile_workflow(
        w.workflow(name="json", version="1", input=Document, output=Document).flow(
            w.typed_step(
                "identity",
                lambda data: data,
                input_schema=Document,
                output_schema=Document,
                artifact_version="1",
            )
        )
    )
    if mode == "persisted":
        service = w.RunService(w.WorkflowRegistry([graph]), w.InMemoryRunStore(), "revision-1")
        accepted = service.admit(name="json", version="1", input_data={"value": "[1,2]"})
        terminal = service.execute(accepted.run_id, expected_version=accepted.version)
        assert terminal.state is w.RunState.SUCCEEDED
        assert Document.model_validate(terminal.output_data).value == [1, 2]
    elif mode == "direct":
        result = w.run_workflow(graph, {"value": "[1,2]"})
        assert result.status is w.RunStatus.COMPLETE
        assert result.output.value == [1, 2]
    else:

        class Envelope(BaseModel):
            document: Document

        node = (
            w.subflow("child", graph, output_key="document")
            if mode == "subflow"
            else w.tool_step(
                "tool",
                lambda data: Document.model_validate(data),
                input_schema=Document,
                output_schema=Document,
                output_key="document",
            )
        )
        outer = w.compile_workflow(
            w.workflow(name="outer", version="1", input=Document, output=Envelope).flow(node)
        )
        result = w.run_workflow(outer, {"value": "[1,2]"})
        assert result.status is w.RunStatus.COMPLETE
        assert result.output.document.value == [1, 2]
