from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta

import pytest
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver

from fin_graph.domain.tools import Domain
from fin_graph.evals.market.runner import run_context
from fin_graph.evals.market.scenarios import load_equity_scenario
from fin_graph.harness.runtime import make_analysis_graph
from fin_graph.integrations.analytics.tools import ANALYTICS_TOOLS
from fin_graph.integrations.context import RunContext
from fin_graph.workflows.analysis.graph import AnalysisLimits
from fin_graph.workflows.analysis.services import UnavailableAnalysisServices


class FixtureTools:
    def tools(self, context: RunContext) -> list[BaseTool]:
        return list(ANALYTICS_TOOLS)


def fixture_graph(*, checkpointer=None):
    scenario = load_equity_scenario("001-deep-value")
    reference = datetime.combine(scenario.as_of + timedelta(days=1), time(16), tzinfo=UTC)
    graph = make_analysis_graph(
        UnavailableAnalysisServices(),
        FixtureTools(),
        clock=lambda: reference,
        checkpointer=checkpointer,
    )
    return graph, run_context(scenario), scenario.request()


async def test_default_graph_checkpoints_harness_identity_and_real_tool_trace():
    graph, context, request = fixture_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "analysis"}}
    result = await graph.ainvoke(
        {"raw_request": request, "source_hint": "json"}, context=context, config=config
    )
    assert result.get("harness", {}).get("mode") == "analysis"
    assert result["harness"]["variant"].startswith("sha256:")
    trace = result["harness_trace"]
    analysts = {
        event["call_id"]
        for event in trace
        if event["kind"] == "agent_start" and event["name"].startswith("analyst_")
    }
    calls = [event for event in trace if event["kind"] == "tool_start"]
    assert calls and all(event["parent"] in analysts for event in calls)
    assert all(event["name"].startswith("compute_") for event in calls)
    assert any(event["kind"] == "tool_end" and event["status"] == "ok" for event in trace)
    snapshot = await graph.aget_state(config)
    assert snapshot.values["harness_trace"] == trace
    again = await graph.ainvoke(
        {"raw_request": request, "source_hint": "json"}, context=context, config=config
    )
    assert len(again["harness_trace"]) == len(trace)
    assert not {event["call_id"] for event in trace} & {
        event["call_id"] for event in again["harness_trace"]
    }


async def test_concurrent_analyses_do_not_mix_trace_scopes():
    graph, context, request = fixture_graph()
    first, second = await asyncio.gather(
        graph.ainvoke(
            {"raw_request": request, "source_hint": "json", "run_id": "a"}, context=context
        ),
        graph.ainvoke(
            {"raw_request": request, "source_hint": "json", "run_id": "b"}, context=context
        ),
    )
    assert first.get("harness_trace") and second.get("harness_trace")
    assert not {event["call_id"] for event in first["harness_trace"]} & {
        event["call_id"] for event in second["harness_trace"]
    }


async def test_checkpoint_timestamps_reconstruct_parallel_analyst_interleaving():
    from fin_graph.harness.trace import ExecutionTrace

    fundamentals_started, valuation_finished = asyncio.Event(), asyncio.Event()

    class InterleavedServices(UnavailableAnalysisServices):
        async def analyze(self, task, *args, **kwargs):
            if task.domain == Domain.FUNDAMENTALS:
                fundamentals_started.set()
                await asyncio.wait_for(valuation_finished.wait(), timeout=5)
            else:
                await asyncio.wait_for(fundamentals_started.wait(), timeout=5)
            result = await super().analyze(task, *args, **kwargs)
            if task.domain == Domain.VALUATION:
                valuation_finished.set()
            return result

    scenario = load_equity_scenario("001-deep-value")
    context = run_context(scenario)
    sink = ExecutionTrace()
    graph = make_analysis_graph(
        InterleavedServices(),
        FixtureTools(),
        limits=AnalysisLimits(max_waves=1, max_tasks_per_wave=2, max_total_tasks=2),
        checkpointer=InMemorySaver(),
        trace=sink,
    )
    config = {"configurable": {"thread_id": "interleaved"}}
    before = datetime.now(UTC)
    await graph.ainvoke(
        {"raw_request": scenario.request(), "source_hint": "json"},
        context=context,
        config=config,
    )
    snapshot = await graph.aget_state(config)
    trace = snapshot.values["harness_trace"]
    assert trace and all(event.get("observed_at") for event in trace)
    assert all(before <= datetime.fromisoformat(event["observed_at"]) for event in trace)
    assert len({event["clock_id"] for event in trace}) == 1
    assert {
        (event["call_id"], event["kind"]): (
            event["observed_at"],
            event["clock_id"],
            event["monotonic_ns"],
        )
        for event in trace
    } == {
        (event["call_id"], event["kind"]): (
            event["observed_at"],
            event["clock_id"],
            event["monotonic_ns"],
        )
        for event in sink.events
    }
    analysts = sorted(
        (event for event in trace if event["name"].startswith("analyst_")),
        key=lambda event: event["monotonic_ns"],
    )
    assert [event["kind"] for event in analysts] == [
        "agent_start",
        "agent_start",
        "agent_end",
        "agent_end",
    ]
    assert [event["name"] for event in analysts[2:]] == [
        "analyst_valuation",
        "analyst_fundamentals",
    ]
    assert all(event["status"] == "ok" for event in analysts[2:])


async def test_environment_budget_is_used_by_analysis_factory(monkeypatch):
    monkeypatch.setenv("FIN_ANALYSIS_MAX_WAVES", "1")
    graph, context, request = fixture_graph()
    result = await graph.ainvoke({"raw_request": request, "source_hint": "json"}, context=context)
    assert result["wave"] == 1
    assert result["harness"]["limits"]["max_waves"] == 1


def test_expected_analysis_variant_mismatch_fails_before_execution(monkeypatch):
    monkeypatch.setenv("FIN_ANALYSIS_EXPECTED_VARIANT", "sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="variant"):
        fixture_graph()


async def test_partial_trace_streams_before_cancelled_analyst_returns(monkeypatch):
    started = asyncio.Event()

    async def wait(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(UnavailableAnalysisServices, "analyze", wait)
    graph, context, request = fixture_graph()
    events = []

    async def consume():
        async for event in graph.astream(
            {"raw_request": request, "source_hint": "json"},
            context=context,
            stream_mode="custom",
        ):
            events.append(event)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=5)
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert any(
        event.get("type") == "harness_trace"
        and event["data"]["name"] == "analyst_fundamentals"
        and event["data"]["kind"] == "agent_start"
        for event in events
    )


async def test_resume_retains_trace_and_rejects_changed_analysis_profile(monkeypatch):
    saver = InMemorySaver()
    graph, context, request = fixture_graph(checkpointer=saver)
    config = {"configurable": {"thread_id": "resume"}}
    paused = await graph.ainvoke(
        {"raw_request": request, "source_hint": "json"},
        context=context,
        config=config,
        interrupt_after=["plan"],
    )
    initial = list(paused["harness_trace"])
    monkeypatch.setenv("FIN_ANALYSIS_MAX_WAVES", "1")
    changed, _, _ = fixture_graph(checkpointer=saver)
    with pytest.raises(ValueError, match=r"profile differs.*fields: limits") as exc:
        await changed.ainvoke(None, context=context, config=config)
    assert str(exc.value).count("sha256:") == 2
    assert "FIN_ANALYSIS_*" in str(exc.value)
    monkeypatch.delenv("FIN_ANALYSIS_MAX_WAVES")
    restarted, _, _ = fixture_graph(checkpointer=saver)
    finished = await restarted.ainvoke(None, context=context, config=config)
    assert finished["report"]
    assert finished["harness_trace"][: len(initial)] == initial


async def test_resume_analysis_checkpoint_from_before_harness_migration():
    from fin_graph.workflows.analysis.graph import _compile_analysis_graph

    scenario = load_equity_scenario("001-deep-value")
    context = run_context(scenario)
    saver = InMemorySaver()
    legacy = _compile_analysis_graph(
        UnavailableAnalysisServices(),
        FixtureTools(),
        checkpointer=saver,
    )
    config = {"configurable": {"thread_id": "legacy-analysis"}}
    await legacy.ainvoke(
        {"raw_request": scenario.request(), "source_hint": "json"},
        context=context,
        config=config,
        interrupt_after=["plan"],
    )
    migrated, _, _ = fixture_graph(checkpointer=saver)
    result = await migrated.ainvoke(None, context=context, config=config)
    assert result["report"]
    assert result["harness"]["mode"] == "analysis"


async def test_analysis_manager_persists_partial_trace_without_tool_arguments(
    tmp_path, monkeypatch
):
    from sqlalchemy.ext.asyncio import create_async_engine

    from fin_graph.server.manager import RunManager
    from fin_graph.server.storage import Base, Storage

    started = asyncio.Event()

    async def wait(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(UnavailableAnalysisServices, "analyze", wait)
    graph, context, request = fixture_graph(checkpointer=InMemorySaver())
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trace.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    manager = RunManager(graph, storage, lambda: context)
    try:
        run = await manager.submit(request, "json")
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.sleep(0.05)
        await manager.stop()
        events = await storage.events(run["thread_id"], 0)
        trace = [event["data"] for event in events if event["type"] == "harness_trace"]
        assert any(event["name"] == "analyst_fundamentals" for event in trace)
        assert all("args" not in event and "window" not in event for event in trace)
        assert (await storage.get_run(run["run_id"]))["status"] == "interrupted"
    finally:
        await manager.stop()
        await storage.close()
        await engine.dispose()
