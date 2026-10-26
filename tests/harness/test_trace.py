from __future__ import annotations

import asyncio
from uuid import uuid4

from fin_graph.evals.runner import run_suite
from fin_graph.evals.scenarios import load_suite


async def test_scripted_run_records_actual_delegation_tools_and_parentage(tmp_path):
    result = await run_suite("one", "scripted", 1, tmp_path, scenarios=load_suite("smoke")[:1])
    trace = result["runs"][0]["trace"]
    assert [event["elapsed"] for event in trace] == sorted(event["elapsed"] for event in trace)
    agents = [event for event in trace if event["kind"] == "agent_start"]
    assert {event["name"] for event in agents} >= {
        "planner",
        "specialist_kubernetes",
        "synthesizer",
    }
    tool = next(event for event in trace if event["kind"] == "tool_start")
    assert tool["name"] == "k8s_list_pods"
    assert tool["args"]["namespace"]
    assert tool["parent"] in {a["call_id"] for a in agents if a["name"] == "specialist_kubernetes"}
    assert any(e["kind"] == "tool_end" and e["call_id"] == tool["call_id"] for e in trace)
    assert (tmp_path / "traces" / "eks--000-healthy--1.json").is_file()


async def test_failed_run_keeps_partial_trace(tmp_path, monkeypatch):
    import fin_graph.evals.runner as runner

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(runner.ScriptedServices, "plan", slow)
    result = await run_suite(
        "one", "scripted", 1, tmp_path, scenarios=load_suite("smoke")[:1], case_timeout=0.1
    )
    record = result["runs"][0]
    assert record["status"] == "failed"
    assert any(e["kind"] == "agent_end" and e["status"] == "error" for e in record["trace"])


async def test_concurrent_scopes_are_not_mixed_and_secrets_are_redacted():
    from fin_graph.evals.trace import ExecutionTrace

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

    await asyncio.gather(run("specialist_cloud"), run("specialist_metrics"))
    starts = {e["call_id"]: e["name"] for e in trace.events if e["kind"] == "agent_start"}
    tools = [e for e in trace.events if e["kind"] == "tool_start"]
    assert {starts[e["parent"]] for e in tools} == {"specialist_cloud", "specialist_metrics"}
    assert "secret-value" not in str(trace.events)


def test_tool_error_message_counts_as_failed_execution():
    from langchain_core.messages import ToolMessage

    from fin_graph.evals.trace import ExecutionTrace

    trace = ExecutionTrace()
    call_id = uuid4()
    trace.on_tool_start({"name": "read"}, "{}", run_id=call_id)
    trace.on_tool_end(
        ToolMessage(content="failed", tool_call_id="x", status="error"), run_id=call_id
    )
    assert trace.events[-1]["status"] == "error"


def test_observed_services_only_wrap_investigation_methods():
    from fin_graph.harness.trace import observe_services

    class Services:
        models = ("worker", "fixture")

        async def plan(self, *args):
            return "plan"

    observed = observe_services(Services())  # type: ignore[arg-type]
    assert observed.models == ("worker", "fixture")  # type: ignore[attr-defined]
    assert not hasattr(observed, "synthesize_with_context")
    assert asyncio.iscoroutinefunction(observed.plan)
