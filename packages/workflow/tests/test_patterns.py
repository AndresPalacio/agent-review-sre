"""Composition runs child contracts through the shared runtime."""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_parallel_isolates_branch_state_and_limits_concurrency():
    from bm_workflow.definition import step
    from bm_workflow.patterns import parallel

    active = 0
    peak = 0

    async def branch(state):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        state["items"].append("child")
        await asyncio.sleep(0.01)
        active -= 1
        return {"seen": len(state["items"])}

    node = parallel(
        "branches",
        {str(i): step(f"b{i}", branch) for i in range(7)},
        output_key="results",
        max_concurrency=2,
    )
    original = {"items": []}
    output = await node.handler(original)
    assert original == {"items": []}
    assert peak == 2
    assert [value["seen"] for value in output["results"].values()] == [1] * 7


@pytest.mark.asyncio
async def test_parallel_cancels_siblings_on_failure():
    from bm_workflow.definition import step
    from bm_workflow.patterns import parallel

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow(state):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def broken(state):
        await started.wait()
        raise ValueError("branch failed")

    node = parallel(
        "branches",
        {"slow": step("slow", slow), "broken": step("bad", broken)},
        output_key="results",
    )
    from bm_workflow.errors import ExecutionError

    with pytest.raises(ExecutionError):
        await node.handler({})
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_map_preserves_order_and_uses_bounded_worker_tasks():
    from bm_workflow.definition import step
    from bm_workflow.patterns import map_each

    workers = set()

    async def body(state):
        workers.add(asyncio.current_task())
        await asyncio.sleep((4 - state["item"] % 4) * 0.001)
        return {"answer": state["item"] * 2}

    node = map_each(
        "map", step("double", body), items_key="numbers", output_key="answers", max_concurrency=3
    )
    output = await node.handler({"numbers": list(range(25)), "parent": True})
    assert [item["answer"] for item in output["answers"]] == list(range(0, 50, 2))
    assert len(workers) == 3


@pytest.mark.asyncio
async def test_map_rejects_oversized_input_before_running_body():
    from bm_workflow.definition import step
    from bm_workflow.patterns import map_each

    def forbidden(state):
        pytest.fail("body must not run")

    node = map_each(
        "map", step("body", forbidden), items_key="items", output_key="results", max_items=2
    )
    with pytest.raises(ValueError, match="max_items"):
        await node.handler({"items": [1, 2, 3]})


@pytest.mark.asyncio
async def test_parallel_sequences_merge_child_updates():
    from bm_workflow.definition import step
    from bm_workflow.patterns import parallel, sequence

    node = parallel(
        "p",
        {
            "sum": sequence(
                step("first", lambda s: {"n": 2}), step("second", lambda s: {"n": s["n"] + 3})
            )
        },
        output_key="result",
    )
    assert await node.handler({}) == {"result": {"sum": {"n": 5}}}


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_worker_limits_are_rejected(limit):
    from bm_workflow.definition import step
    from bm_workflow.patterns import map_each, parallel

    body = step("body", lambda s: {})
    with pytest.raises(ValueError):
        parallel("p", {"a": body}, output_key="out", max_concurrency=limit)
    with pytest.raises(ValueError):
        map_each("m", body, items_key="items", output_key="out", max_concurrency=limit)


def test_nested_composition_dry_run_intercepts_each_external_child():
    from pydantic import BaseModel

    import bm_workflow as w
    from bm_workflow.patterns import map_each, parallel

    class Input(BaseModel):
        numbers: list[int]

    class Output(BaseModel):
        groups: dict

    def forbidden(_):
        pytest.fail("external child must not execute")

    mapped = map_each(
        "map",
        w.step("external", forbidden, kind="external"),
        items_key="numbers",
        output_key="answers",
    )
    graph = w.compile_workflow(
        w.workflow(name="nested-dry", version="1", input=Input, output=Output).flow(
            parallel("branches", {"one": mapped}, output_key="groups")
        )
    )
    report = w.dry_run(graph, {"numbers": [1, 2]}, fixtures={"external": {"value": 9}})
    answers = report.result.output.groups["one"]["answers"]
    assert [answer["value"] for answer in answers] == [9, 9]
    assert len([event for event in report.events if event.node_id == "external"]) == 2


@pytest.mark.asyncio
async def test_subflow_runs_with_own_identity_and_preserves_parent_deadline():
    from pydantic import BaseModel

    import bm_workflow as w
    from bm_workflow.patterns import subflow
    from bm_workflow.runtime import current_context

    class Input(BaseModel):
        value: int

    class Output(BaseModel):
        nested: Input

    observed = []

    async def inspect_context(state):
        observed.append(current_context())
        return {"value": state["value"] + 1}

    child = w.compile_workflow(
        w.workflow(name="child", version="1", input=Input, output=Input).flow(
            w.step("inspect", inspect_context)
        )
    )
    parent = w.compile_workflow(
        w.workflow(name="parent", version="1", input=Input, output=Output).flow(
            subflow("child", child, output_key="nested")
        )
    )
    result = await w.arun_workflow(parent, {"value": 1})
    assert result.output == Output(nested=Input(value=2))
    assert observed[0].name == "child"
    assert observed[0].definition_digest == child.definition_digest
    assert observed[0].cancellation is result.context.cancellation
    assert observed[0].deadline == result.context.deadline


@pytest.mark.parametrize("pattern", ["parallel", "map"])
@pytest.mark.parametrize("failure", ["timeout", "cancel"])
@pytest.mark.asyncio
async def test_composition_preserves_child_timeout_and_cancellation(pattern, failure):
    from pydantic import BaseModel

    import bm_workflow as w
    from bm_workflow.patterns import map_each, parallel

    class State(BaseModel):
        items: list[int]

    async def body(_):
        if failure == "cancel":
            raise asyncio.CancelledError
        await asyncio.Event().wait()

    child = w.step("child", body, policy=w.ExecutionPolicy(timeout_seconds=0.01))
    composite = (
        parallel("parent", {"branch": child}, output_key="out")
        if pattern == "parallel"
        else map_each("parent", child, items_key="items", output_key="out")
    )
    graph = w.compile_workflow(
        w.workflow(name="cause", version="1", input=State, output=State).flow(composite)
    )
    result = await w.arun_workflow(graph, {"items": [1]})
    assert result.status is (
        w.RunStatus.CANCELLED if failure == "cancel" else w.RunStatus.TIMED_OUT
    )


@pytest.mark.asyncio
async def test_subflow_preserves_child_node_timeout_status():
    from pydantic import BaseModel

    import bm_workflow as w
    from bm_workflow.patterns import subflow

    class State(BaseModel):
        value: int

    async def blocked(_):
        await asyncio.Event().wait()

    child = w.compile_workflow(
        w.workflow(name="slow-child", version="1", input=State, output=State).flow(
            w.step("blocked", blocked, policy=w.ExecutionPolicy(timeout_seconds=0.01))
        )
    )
    parent = w.compile_workflow(
        w.workflow(name="parent", version="1", input=State, output=State).flow(
            subflow("child", child, output_key="out")
        )
    )
    result = await w.arun_workflow(parent, {"value": 1})
    assert result.status is w.RunStatus.TIMED_OUT
    assert result.cause.node_id == "blocked"


def test_empty_compositions_are_rejected_before_execution():
    from bm_workflow.patterns import parallel, sequence

    with pytest.raises(ValueError):
        sequence()
    with pytest.raises(ValueError):
        parallel("empty", {}, output_key="out")
    with pytest.raises(ValueError):
        parallel("empty-branch", {"branch": []}, output_key="out")
