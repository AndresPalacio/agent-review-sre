from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from sre_graph.evals.fixtures import FixtureBackend
from sre_graph.evals.runner import ScriptedServices
from sre_graph.evals.scenarios import load_suite
from sre_graph.graphs.investigation.graph import make_investigation_graph


def fixture_graph(*, checkpointer=None):
    scenario = load_suite("smoke")[0]
    backend = FixtureBackend(scenario)
    graph = make_investigation_graph(
        ScriptedServices(scenario),
        backend.registry,
        clock=lambda: scenario.reference_time,
        checkpointer=checkpointer,
    )
    return graph, backend.context, scenario.alert


async def test_default_graph_checkpoints_harness_identity_and_real_tool_trace():
    graph, context, alert = fixture_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "incident"}}
    result = await graph.ainvoke({"raw_alert": alert}, context=context, config=config)
    assert result.get("harness", {}).get("mode") == "incident"
    assert result["harness"]["variant"].startswith("sha256:")
    trace = result["harness_trace"]
    specialist = {e["call_id"] for e in trace if e["name"] == "specialist_kubernetes"}
    calls = [e for e in trace if e["kind"] == "tool_start"]
    assert calls and all(e["parent"] in specialist for e in calls)
    assert any(e["kind"] == "tool_end" and e["status"] == "ok" for e in trace)
    snapshot = await graph.aget_state(config)
    assert snapshot.values["harness_trace"] == trace
    again = await graph.ainvoke({"raw_alert": alert}, context=context, config=config)
    assert len(again["harness_trace"]) == len(trace)
    assert not {e["call_id"] for e in trace} & {e["call_id"] for e in again["harness_trace"]}


async def test_concurrent_incidents_do_not_mix_trace_scopes():
    graph, context, alert = fixture_graph()
    first, second = await asyncio.gather(
        graph.ainvoke({"raw_alert": alert, "run_id": "a"}, context=context),
        graph.ainvoke({"raw_alert": alert, "run_id": "b"}, context=context),
    )
    assert first.get("harness_trace") and second.get("harness_trace")
    assert not {e["call_id"] for e in first["harness_trace"]} & {
        e["call_id"] for e in second["harness_trace"]
    }


async def test_checkpoint_timestamps_reconstruct_parallel_specialist_interleaving():
    from datetime import UTC, datetime

    from tests.graphs.investigation.test_graph import FakeServices, ToolSource

    from sre_graph.domain.tools import Domain
    from sre_graph.graphs.investigation.graph import InvestigationLimits
    from sre_graph.harness.runtime import make_investigation_graph
    from sre_graph.harness.trace import ExecutionTrace
    from sre_graph.integrations.context import RunContext

    metrics_started, logs_finished = asyncio.Event(), asyncio.Event()

    class InterleavedServices(FakeServices):
        async def investigate(self, task, *args, **kwargs):
            if task.domain == Domain.METRICS:
                metrics_started.set()
                await asyncio.wait_for(logs_finished.wait(), timeout=5)
            else:
                await asyncio.wait_for(metrics_started.wait(), timeout=5)
            result = await super().investigate(task, *args, **kwargs)
            if task.domain == Domain.LOGS:
                logs_finished.set()
            return result

    sink = ExecutionTrace()
    graph = make_investigation_graph(
        InterleavedServices(),
        ToolSource([Domain.METRICS, Domain.LOGS]),
        limits=InvestigationLimits(max_waves=1),
        checkpointer=InMemorySaver(),
        trace=sink,
    )
    config = {"configurable": {"thread_id": "interleaved"}}
    before = datetime.now(UTC)
    await graph.ainvoke(
        {"raw_alert": {"title": "API CPU high"}}, context=RunContext(), config=config
    )
    snapshot = await graph.aget_state(config)
    trace = snapshot.values["harness_trace"]
    assert trace and all(event.get("observed_at") for event in trace)
    assert all(before <= datetime.fromisoformat(event["observed_at"]) for event in trace)
    assert len({event["clock_id"] for event in trace}) == 1
    assert {
        (e["call_id"], e["kind"]): (e["observed_at"], e["clock_id"], e["monotonic_ns"])
        for e in trace
    } == {
        (e["call_id"], e["kind"]): (e["observed_at"], e["clock_id"], e["monotonic_ns"])
        for e in sink.events
    }
    specialists = sorted(
        (e for e in trace if e["name"].startswith("specialist_")),
        key=lambda event: event["monotonic_ns"],
    )
    assert [e["kind"] for e in specialists] == [
        "agent_start",
        "agent_start",
        "agent_end",
        "agent_end",
    ]
    assert [e["name"] for e in specialists[2:]] == ["specialist_logs", "specialist_metrics"]
    assert all(e["status"] == "ok" for e in specialists[2:])


async def test_environment_budget_is_used_by_compatibility_factory(monkeypatch):
    monkeypatch.setenv("SRE_HARNESS_MAX_WAVES", "1")
    graph, context, alert = fixture_graph()
    result = await graph.ainvoke({"raw_alert": alert}, context=context)
    assert result["wave"] == 1
    assert result["harness"]["limits"]["max_waves"] == 1


def test_expected_variant_mismatch_fails_before_execution(monkeypatch):
    monkeypatch.setenv("SRE_HARNESS_EXPECTED_VARIANT", "sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="variant"):
        fixture_graph()


async def test_partial_trace_streams_before_cancelled_node_returns(monkeypatch):
    started = asyncio.Event()

    async def wait(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ScriptedServices, "investigate", wait)
    graph, context, alert = fixture_graph()
    events = []

    async def consume():
        async for event in graph.astream(
            {"raw_alert": alert}, context=context, stream_mode="custom"
        ):
            events.append(event)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=5)
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert any(
        e.get("type") == "harness_trace"
        and e["data"]["name"] == "specialist_kubernetes"
        and e["data"]["kind"] == "agent_start"
        for e in events
    )


async def test_resume_retains_trace_and_rejects_changed_profile(monkeypatch):
    saver = InMemorySaver()
    graph, context, alert = fixture_graph(checkpointer=saver)
    config = {"configurable": {"thread_id": "resume"}}
    paused = await graph.ainvoke(
        {"raw_alert": alert}, context=context, config=config, interrupt_after=["plan"]
    )
    initial = list(paused["harness_trace"])
    monkeypatch.setenv("SRE_HARNESS_MAX_WAVES", "1")
    changed, _, _ = fixture_graph(checkpointer=saver)
    with pytest.raises(ValueError, match=r"profile differs.*fields: limits") as exc:
        await changed.ainvoke(None, context=context, config=config)
    assert str(exc.value).count("sha256:") == 2
    monkeypatch.delenv("SRE_HARNESS_MAX_WAVES")
    restarted, _, _ = fixture_graph(checkpointer=saver)
    finished = await restarted.ainvoke(None, context=context, config=config)
    assert finished["report"]
    assert finished["harness_trace"][: len(initial)] == initial


async def test_resume_checkpoint_from_before_harness_migration():
    from sre_graph.graphs.investigation.graph import _compile_investigation_graph

    scenario = load_suite("smoke")[0]
    backend = FixtureBackend(scenario)
    saver = InMemorySaver()
    legacy = _compile_investigation_graph(
        ScriptedServices(scenario),
        backend.registry,
        clock=lambda: scenario.reference_time,
        checkpointer=saver,
    )
    config = {"configurable": {"thread_id": "legacy"}}
    await legacy.ainvoke(
        {"raw_alert": scenario.alert},
        context=backend.context,
        config=config,
        interrupt_after=["plan"],
    )
    migrated, _, _ = fixture_graph(checkpointer=saver)
    result = await migrated.ainvoke(None, context=backend.context, config=config)
    assert result["report"]
    assert result["harness"]["mode"] == "incident"


async def test_manager_persists_partial_trace_without_tool_arguments(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine

    from sre_graph.server.manager import RunManager
    from sre_graph.server.storage import Base, Storage

    started = asyncio.Event()

    async def wait(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ScriptedServices, "investigate", wait)
    graph, context, alert = fixture_graph(checkpointer=InMemorySaver())
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trace.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    manager = RunManager(graph, storage, lambda: context)
    try:
        run = await manager.submit(alert, None)
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.sleep(0.05)
        await manager.stop()
        events = await storage.events(run["thread_id"], 0)
        trace = [e["data"] for e in events if e["type"] == "harness_trace"]
        assert any(e["name"] == "specialist_kubernetes" for e in trace)
        assert all("args" not in e and "window" not in e for e in trace)
        assert (await storage.get_run(run["run_id"]))["status"] == "interrupted"
    finally:
        await manager.stop()
        await storage.close()
        await engine.dispose()
