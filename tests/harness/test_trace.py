from __future__ import annotations

import asyncio
from uuid import uuid4

from fin_graph.evals.market.runner import run_equity_suite
from fin_graph.evals.market.scenarios import load_equity_suite


async def test_scripted_run_records_actual_delegation_tools_and_parentage(tmp_path):
    result = await run_equity_suite(
        "one", "scripted", 1, tmp_path, scenarios=load_equity_suite("smoke")[:1]
    )
    trace = result["runs"][0]["trace"]
    assert [event["elapsed"] for event in trace] == sorted(event["elapsed"] for event in trace)
    agents = [event for event in trace if event["kind"] == "agent_start"]
    assert {event["name"] for event in agents} >= {
        "planner",
        "analyst_fundamentals",
        "synthesizer",
    }
    tool = next(event for event in trace if event["kind"] == "tool_start")
    assert tool["name"].startswith("compute_")
    assert tool["args"]["ticker"] == "DEEP"
    owner = next(agent for agent in agents if agent["call_id"] == tool["parent"])
    assert owner["name"].startswith("analyst_")
    assert any(e["kind"] == "tool_end" and e["call_id"] == tool["call_id"] for e in trace)
    assert (tmp_path / "traces" / "equities--001-deep-value--1.json").is_file()


async def test_failed_run_keeps_partial_trace(tmp_path, monkeypatch):
    from fin_graph.graphs.analysis.services import UnavailableAnalysisServices

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(UnavailableAnalysisServices, "plan", slow)
    result = await run_equity_suite(
        "one",
        "scripted",
        1,
        tmp_path,
        scenarios=load_equity_suite("smoke")[:1],
        case_timeout=0.1,
    )
    record = result["runs"][0]
    assert record["status"] == "failed"
    assert any(e["kind"] == "agent_end" and e["status"] == "error" for e in record["trace"])


async def test_concurrent_scopes_are_not_mixed_and_secrets_are_redacted():
    from fin_graph.harness.trace import ExecutionTrace

    trace = ExecutionTrace()

    async def run(name):
        async def action():
            await asyncio.sleep(0)
            call_id = uuid4()
            trace.on_tool_start(
                {"name": "read"}, "", run_id=call_id, inputs={"api_key": "secret-value"}
            )
            trace.on_tool_end("done", run_id=call_id)

        await trace.agent(name, action)

    await asyncio.gather(run("analyst_valuation"), run("analyst_fundamentals"))
    starts = {e["call_id"]: e["name"] for e in trace.events if e["kind"] == "agent_start"}
    tools = [e for e in trace.events if e["kind"] == "tool_start"]
    assert {starts[e["parent"]] for e in tools} == {
        "analyst_valuation",
        "analyst_fundamentals",
    }
    assert "secret-value" not in str(trace.events)


def test_tool_error_message_counts_as_failed_execution():
    from langchain_core.messages import ToolMessage

    from fin_graph.harness.trace import ExecutionTrace

    trace = ExecutionTrace()
    call_id = uuid4()
    trace.on_tool_start({"name": "read"}, "{}", run_id=call_id)
    trace.on_tool_end(
        ToolMessage(content="failed", tool_call_id="x", status="error"), run_id=call_id
    )
    assert trace.events[-1]["status"] == "error"


def test_observed_services_only_wrap_analysis_methods():
    from fin_graph.harness.trace import observe_analysis_services

    class Services:
        models = ("worker", "fixture")

        async def plan(self, *args):
            return "plan"

    observed = observe_analysis_services(Services())
    assert observed.models == ("worker", "fixture")  # type: ignore[attr-defined]
    assert not hasattr(observed, "decide")
    assert asyncio.iscoroutinefunction(observed.plan)
