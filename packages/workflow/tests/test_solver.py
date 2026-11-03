from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

pytest.importorskip("deepagents")
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr


class Solution(BaseModel):
    value: int


class ScriptedModel(BaseChatModel):
    responses: list[AIMessage]
    _index: int = PrivateAttr(default=0)

    @property
    def _llm_type(self):
        return "offline-script"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = self.responses[min(self._index, len(self.responses) - 1)]
        self._index += 1
        return ChatResult(generations=[ChatGeneration(message=result.model_copy(deep=True))])


def candidate(value):
    return AIMessage(
        content="", tool_calls=[{"name": "Solution", "args": {"value": value}, "id": f"s-{value}"}]
    )


def problem():
    from bm_workflow.agents import ProblemSpec

    return ProblemSpec(
        id="repair", goal="Make value 2", acceptance_criteria={"actual": "Actual value is 2"}
    )


async def test_native_action_checked_and_repaired():
    from bm_workflow.agents import (
        SolverContext,
        VerificationCheck,
        VerificationReport,
        build_solver_agent,
    )

    environment = {"value": 0}

    def set_value(value: int) -> str:
        """Set actual environment value."""
        environment["value"] = value
        return "done"

    def verify(spec, proposal, context):
        return VerificationReport(
            verdict="verified" if environment["value"] == 2 else "failed",
            checks=[VerificationCheck(criterion_id="actual", passed=environment["value"] == 2)],
        )

    model = ScriptedModel(
        responses=[
            candidate(2),
            AIMessage(
                content="", tool_calls=[{"name": "set_value", "args": {"value": 2}, "id": "effect"}]
            ),
            candidate(2),
        ]
    )
    graph = build_solver_agent(
        model=model, tools=[set_value], solution_schema=Solution, verifier=verify
    )
    output = await graph.ainvoke(
        {"messages": [("user", "repair")]}, context=SolverContext(problem=problem())
    )
    result = output["solver_result"]
    assert result.verified
    assert result.attempts == 2
    assert result.usage.model_calls == 3
    assert result.usage.tool_calls == 1
    assert environment["value"] == 2


@pytest.mark.parametrize(
    "report",
    [
        {"verdict": "verified"},
        {"verdict": "verified", "checks": [{"criterion_id": "fake", "passed": True}]},
        {"verdict": "verified", "checks": [{"criterion_id": "actual", "passed": False}]},
        {
            "verdict": "verified",
            "checks": [{"criterion_id": "actual", "passed": True}],
            "missing_requirements": ["access"],
        },
    ],
)
async def test_forged_or_incomplete_verification_never_approves(report):
    from bm_workflow.agents import (
        SolverContext,
        SolverLimits,
        VerificationReport,
        build_solver_agent,
    )

    graph = build_solver_agent(
        model=ScriptedModel(responses=[candidate(2)]),
        tools=[],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(**report),
        limits=SolverLimits(max_repairs=0),
    )
    output = await graph.ainvoke(
        {"messages": [("user", "repair")]}, context=SolverContext(problem=problem())
    )
    assert not output["solver_result"].verified


async def test_verifier_exception_and_timeout_never_approve():
    from bm_workflow.agents import SolverContext, SolverLimits, build_solver_agent

    async def verify(*args):
        await asyncio.sleep(1)
        raise RuntimeError("secret")

    graph = build_solver_agent(
        model=ScriptedModel(responses=[candidate(2)]),
        tools=[],
        solution_schema=Solution,
        verifier=verify,
        limits=SolverLimits(verifier_timeout_seconds=0.01, max_repairs=0),
    )
    output = await graph.ainvoke(
        {"messages": [("user", "repair")]}, context=SolverContext(problem=problem())
    )
    assert output["solver_result"].verification.verdict == "inconclusive"
    assert not output["solver_result"].verified


async def test_budget_stops_before_effect_and_repair_exhaustion():
    from bm_workflow.agents import (
        SolverContext,
        SolverLimits,
        VerificationReport,
        build_solver_agent,
    )

    effects = []

    def action() -> str:
        """Perform effect."""
        effects.append(1)
        return "done"

    graph = build_solver_agent(
        model=ScriptedModel(
            responses=[
                AIMessage(content="", tool_calls=[{"name": "action", "args": {}, "id": "effect"}])
            ]
        ),
        tools=[action],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(verdict="failed"),
        limits=SolverLimits(max_tool_calls=0),
    )
    output = await graph.ainvoke(
        {"messages": [("user", "repair")]}, context=SolverContext(problem=problem())
    )
    assert not output["solver_result"].verified
    assert output["solver_result"].usage.tool_calls == 0
    assert effects == []

    graph = build_solver_agent(
        model=ScriptedModel(responses=[candidate(2)]),
        tools=[],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(verdict="failed"),
    )
    output = await graph.ainvoke(
        {"messages": [("user", "repair")]}, context=SolverContext(problem=problem())
    )
    assert output["solver_result"].attempts == 3
    assert output["solver_result"].usage.model_calls == 3


async def test_parallel_runs_preserve_host_context_and_isolate_usage():
    import threading

    from bm_workflow.agents import (
        SolverContext,
        VerificationCheck,
        VerificationReport,
        build_solver_agent,
    )

    class Host:
        def __init__(self):
            self.lock = threading.Lock()

    hosts = [Host(), Host()]
    seen = []

    async def verify(spec, proposal, context):
        seen.append(context.host_context)
        await asyncio.sleep(0.01)
        return VerificationReport(
            verdict="verified", checks=[VerificationCheck(criterion_id="actual", passed=True)]
        )

    graph = build_solver_agent(
        model=ScriptedModel(responses=[candidate(2)]),
        tools=[],
        solution_schema=Solution,
        verifier=verify,
    )
    outputs = await asyncio.gather(
        *(
            graph.ainvoke(
                {"messages": [("user", "repair")]},
                context=SolverContext(problem=problem(), host_context=host),
            )
            for host in hosts
        )
    )
    assert all(sum(item is host for item in seen) == 2 for host in hosts)
    assert all(o["solver_result"].usage.model_calls == 1 for o in outputs)


def test_problem_is_immutable_and_requires_criteria():
    from pydantic import ValidationError

    from bm_workflow.agents import ProblemSpec

    with pytest.raises(ValidationError):
        ProblemSpec(id="x", goal="x", acceptance_criteria={})
    spec = problem()
    with pytest.raises(TypeError):
        spec.acceptance_criteria["forged"] = "approve"


async def test_child_calls_share_parent_budget():
    from bm_workflow.agents import (
        SolverContext,
        SolverLimits,
        VerificationReport,
        build_solver_agent,
    )

    parent = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "inspect", "subagent_type": "inspector"},
                        "id": "delegate",
                    }
                ],
            ),
            candidate(2),
        ]
    )
    child = ScriptedModel(responses=[AIMessage(content="inspected")])
    graph = build_solver_agent(
        model=parent,
        tools=[],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(verdict="failed"),
        specialists=[
            {"name": "inspector", "description": "inspect only", "model": child, "tools": []}
        ],
        limits=SolverLimits(max_model_calls=2),
    )
    output = await graph.ainvoke(
        {"messages": [("user", "inspect")]}, context=SolverContext(problem=problem())
    )
    result = output["solver_result"]
    assert result.usage.model_calls == 2
    assert parent._index == 1
    assert child._index == 1
    assert not result.verified


async def test_final_recheck_detects_mutation_and_blocked_does_not_repair():
    from bm_workflow.agents import (
        SolverContext,
        VerificationCheck,
        VerificationReport,
        build_solver_agent,
    )

    checks = []

    def verify(*args):
        checks.append(1)
        passed = len(checks) == 1
        return VerificationReport(
            verdict="verified" if passed else "failed",
            checks=[VerificationCheck(criterion_id="actual", passed=passed)],
        )

    graph = build_solver_agent(
        model=ScriptedModel(responses=[candidate(2)]),
        tools=[],
        solution_schema=Solution,
        verifier=verify,
    )
    result = (
        await graph.ainvoke(
            {"messages": [("user", "inspect")]}, context=SolverContext(problem=problem())
        )
    )["solver_result"]
    assert not result.verified
    assert len(checks) == 2
    graph = build_solver_agent(
        model=ScriptedModel(responses=[candidate(2)]),
        tools=[],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(
            verdict="blocked", missing_requirements=["database access"]
        ),
    )
    result = (
        await graph.ainvoke(
            {"messages": [("user", "inspect")]}, context=SolverContext(problem=problem())
        )
    )["solver_result"]
    assert result.verification.verdict == "blocked"
    assert result.attempts == 1


async def test_workflow_cancellation_interrupts_verifier_and_context_cannot_be_reused():
    from bm_workflow.agents import SolverContext, VerificationReport, build_solver_agent
    from bm_workflow.runtime import RunContext

    started = asyncio.Event()

    async def verify(*args):
        started.set()
        await asyncio.sleep(20)
        return VerificationReport(verdict="verified")

    run = RunContext(name="test", version="1", definition_digest="d")
    context = SolverContext(problem=problem(), workflow_context=run)
    graph = build_solver_agent(
        model=ScriptedModel(responses=[candidate(2)]),
        tools=[],
        solution_schema=Solution,
        verifier=verify,
    )
    invocation = asyncio.create_task(
        graph.ainvoke({"messages": [("user", "inspect")]}, context=context)
    )
    await asyncio.wait_for(started.wait(), 2)
    run.cancellation.set()
    from bm_workflow.errors import ExecutionError

    with pytest.raises(ExecutionError, match="cancelled"):
        await asyncio.wait_for(invocation, 2)
    with pytest.raises(ValueError, match="cannot be reused"):
        await graph.ainvoke({"messages": [("user", "inspect")]}, context=context)


def test_problem_inputs_snapshot_is_recursively_immutable():
    from bm_workflow.agents import ProblemSpec

    original = {"nested": {"values": [1, 2]}}
    spec = ProblemSpec(id="x", goal="x", inputs=original, acceptance_criteria={"c": "check"})
    original["nested"]["values"].append(3)
    assert list(spec.inputs["nested"]["values"]) == [1, 2]
    with pytest.raises(TypeError):
        spec.inputs["nested"]["new"] = 1
    assert spec.model_dump(mode="json")["inputs"] == {"nested": {"values": [1, 2]}}


async def test_native_summary_reserves_separate_budget():
    from bm_workflow.agents import (
        SolverContext,
        SolverLimits,
        VerificationReport,
        build_solver_agent,
    )

    # Enough old history to exceed the native fixed-token fallback trigger.
    messages = [("user", "old history " * 1000), ("assistant", "old response")] * 70
    messages.append(("user", "finish"))
    model = ScriptedModel(responses=[candidate(2)])
    graph = build_solver_agent(
        model=model,
        tools=[],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(verdict="failed"),
        limits=SolverLimits(max_summary_calls=0),
    )
    output = await graph.ainvoke({"messages": messages}, context=SolverContext(problem=problem()))
    assert output["solver_result"].usage.summary_calls == 0
    assert output["solver_result"].usage.model_calls == 0
    assert model._index == 0
    assert not output["solver_result"].verified


async def test_filesystem_offload_obeys_deadline():
    import time

    from deepagents.backends import StateBackend

    from bm_workflow.agents import (
        SolverContext,
        SolverLimits,
        VerificationReport,
        build_solver_agent,
    )

    class SlowBackend(StateBackend):
        async def awrite(self, *args, **kwargs):
            await asyncio.sleep(10)
            return await super().awrite(*args, **kwargs)

    def large() -> str:
        """Return a large body forcing native filesystem offload."""
        return "x" * 200000

    graph = build_solver_agent(
        model=ScriptedModel(
            responses=[
                AIMessage(content="", tool_calls=[{"name": "large", "args": {}, "id": "large"}])
            ]
        ),
        tools=[large],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(verdict="failed"),
        backend=SlowBackend(),
        limits=SolverLimits(timeout_seconds=0.1),
    )
    start = time.monotonic()
    output = await asyncio.wait_for(
        graph.ainvoke(
            {"messages": [("user", "inspect")]}, context=SolverContext(problem=problem())
        ),
        1,
    )
    assert time.monotonic() - start < 1
    assert not output["solver_result"].verified


def test_remote_specialists_rejected_and_readonly_verifier_tools_supported():
    from langchain_core.tools import tool

    from bm_workflow.agents import VerificationReport, build_solver_agent

    @tool
    def observe() -> str:
        """Read actual state."""
        return "state"

    observe.metadata = {"read_only": True}
    options = dict(
        model=ScriptedModel(responses=[candidate(2)]),
        tools=[],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(verdict="failed"),
    )
    with pytest.raises(ValueError, match="specialist"):
        build_solver_agent(
            **options,
            specialists=[{"name": "remote", "description": "remote", "graph_id": "remote"}],
        )
    build_solver_agent(
        **options, specialists=[{"name": "verifier", "description": "readonly", "tools": [observe]}]
    )


async def test_initial_skill_loading_obeys_deadline():
    from deepagents.backends import StateBackend

    from bm_workflow.agents import (
        SolverContext,
        SolverLimits,
        VerificationReport,
        build_solver_agent,
    )

    class SlowSkillsBackend(StateBackend):
        async def als(self, *args, **kwargs):
            await asyncio.sleep(10)
            return []

    graph = build_solver_agent(
        model=ScriptedModel(responses=[candidate(2)]),
        tools=[],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(verdict="failed"),
        backend=SlowSkillsBackend(),
        skills=["/skills/"],
        limits=SolverLimits(timeout_seconds=0.05),
    )
    output = await asyncio.wait_for(
        graph.ainvoke(
            {"messages": [("user", "inspect")]}, context=SolverContext(problem=problem())
        ),
        1,
    )
    assert not output["solver_result"].verified
    assert output["solver_result"].usage.model_calls == 0


async def test_solver_step_preserves_json_aliases_invocation_and_rejects_pause():
    import threading

    from pydantic import Field, Json

    from bm_workflow import AgentInvocation, AgentManifest
    from bm_workflow.agents import SolverResult, VerificationReport, solver_step

    class RichSolution(BaseModel):
        values: Json[list[int]] = Field(alias="result_values")

    host = {"lock": threading.Lock()}
    calls = []

    class Graph:
        async def ainvoke(self, payload, *, config, context):
            calls.append(context)
            assert config == {"tags": ["host"]}
            assert context.host_context is host
            return {
                "solver_result": SolverResult(
                    candidate=RichSolution(result_values="[1,2]"),
                    verification=VerificationReport(verdict="inconclusive"),
                )
            }

    node = solver_step(
        "solver",
        Graph(),
        solution_schema=RichSolution,
        output_key="answer",
        problem_mapper=lambda _: problem(),
        invocation_factory=lambda state, context: AgentInvocation(
            config={"tags": ["host"]}, context=host
        ),
        manifest=AgentManifest(revisions={"verifier": "1"}),
    )
    output = await node.handler({})
    assert output["answer"]["candidate"]["values"] == "[1,2]"
    assert len(calls) == 1
    assert node.metadata["agent_manifest"] == {"revisions": {"verifier": "1"}}

    class Paused:
        async def ainvoke(self, *args, **kwargs):
            return {"__interrupt__": ["pause"]}

    node = solver_step(
        "solver",
        Paused(),
        solution_schema=Solution,
        output_key="answer",
        problem_mapper=lambda _: problem(),
    )
    with pytest.raises(ValueError, match="pauses"):
        await node.handler({})


def test_solver_dry_run_skips_every_live_boundary():
    import bm_workflow as w
    from bm_workflow.agents import SolverResult, VerificationReport, solver_step

    class Input(BaseModel):
        value: int

    class Output(BaseModel):
        answer: SolverResult[Solution]

    def forbidden(*args):
        pytest.fail("dry run invoked live dependency")

    node = solver_step(
        "solver",
        forbidden,
        solution_schema=Solution,
        output_key="answer",
        problem_mapper=forbidden,
        invocation_factory=forbidden,
    )
    flow = w.compile_workflow(
        w.workflow(name="solver-dry", version="1", input=Input, output=Output).flow(node)
    )
    fixture = SolverResult(
        candidate=Solution(value=2), verification=VerificationReport(verdict="inconclusive")
    ).model_dump()
    report = w.dry_run(flow, {"value": 2}, fixtures={"solver": {"answer": fixture}})
    assert report.result.status == w.RunStatus.COMPLETE
    assert not report.result.output.answer.verified


@pytest.mark.parametrize("mode", ["exception", "sync_timeout"])
async def test_sync_verifier_failures_are_safe(mode):
    import time

    from bm_workflow.agents import SolverContext, SolverLimits, build_solver_agent

    def verify(*args):
        if mode == "sync_timeout":
            time.sleep(0.1)
        raise RuntimeError("private credential")

    graph = build_solver_agent(
        model=ScriptedModel(responses=[candidate(2)]),
        tools=[],
        solution_schema=Solution,
        verifier=verify,
        limits=SolverLimits(verifier_timeout_seconds=0.01),
    )
    output = await graph.ainvoke(
        {"messages": [("user", "inspect")]}, context=SolverContext(problem=problem())
    )
    assert not output["solver_result"].verified
    assert "private credential" not in output["solver_result"].model_dump_json()


async def test_parallel_specialist_concurrency_and_observer_run_identity():
    from bm_workflow.agents import (
        SolverContext,
        SolverLimits,
        VerificationReport,
        build_solver_agent,
    )

    events = []
    parent = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "observe", "subagent_type": "inspector"},
                        "id": f"task-{i}",
                    }
                    for i in range(3)
                ],
            ),
            candidate(2),
        ]
    )
    child = ScriptedModel(responses=[AIMessage(content="observed")])
    graph = build_solver_agent(
        model=parent,
        tools=[],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(verdict="inconclusive"),
        limits=SolverLimits(max_concurrent_specialists=1),
        specialists=[{"name": "inspector", "description": "observe", "model": child, "tools": []}],
        observer=events.append,
    )
    context = SolverContext(problem=problem())
    output = await graph.ainvoke({"messages": [("user", "inspect")]}, context=context)
    assert child._index == 1
    assert output["solver_result"].usage.model_calls == 3
    assert all(
        event["run_id"] == context.run_id and event["problem_id"] == "repair" for event in events
    )
    events.clear()
    other = SolverContext(problem=problem())
    await graph.ainvoke({"messages": [("user", "inspect")]}, context=other)
    assert context.run_id != other.run_id
    assert all(event["run_id"] == other.run_id for event in events)


def test_default_problem_inputs_are_immutable():
    with pytest.raises(TypeError):
        problem().inputs["new"] = "mutation"


@pytest.mark.parametrize("initial_value", [1, 2])
async def test_mixed_action_and_candidate_execute_before_verification_and_repair(initial_value):
    from bm_workflow.agents import (
        SolverContext,
        VerificationCheck,
        VerificationReport,
        build_solver_agent,
    )

    effects = []
    observations = []

    def set_value(value: int) -> str:
        """Set the actual environment value."""
        effects.append(value)
        return "done"

    def mixed(value, identity):
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "set_value", "args": {"value": value}, "id": f"effect-{identity}"},
                {"name": "Solution", "args": {"value": value}, "id": f"candidate-{identity}"},
            ],
        )

    def verify(spec, proposal, context):
        observations.append(effects[-1] if effects else None)
        passed = bool(effects) and effects[-1] == 2
        return VerificationReport(
            verdict="verified" if passed else "failed",
            checks=[VerificationCheck(criterion_id="actual", passed=passed)],
        )

    graph = build_solver_agent(
        model=ScriptedModel(responses=[mixed(initial_value, 1), mixed(2, 2)]),
        tools=[set_value],
        solution_schema=Solution,
        verifier=verify,
    )
    result = (
        await graph.ainvoke({"messages": [("user", "repair")]}, context=SolverContext(problem()))
    )["solver_result"]
    assert result.verified
    assert effects == ([1, 2] if initial_value == 1 else [2])
    assert observations == ([1, 2, 2] if initial_value == 1 else [2, 2])
    assert result.attempts == (2 if initial_value == 1 else 1)
    assert result.usage.tool_calls == len(effects)
    assert result.usage.model_calls == len(effects)


async def test_mixed_candidate_cannot_skip_exhausted_tool_budget():
    from bm_workflow.agents import (
        SolverContext,
        SolverLimits,
        VerificationCheck,
        VerificationReport,
        build_solver_agent,
    )

    effects = []

    def action() -> str:
        """Perform an action."""
        effects.append(1)
        return "done"

    graph = build_solver_agent(
        model=ScriptedModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "action", "args": {}, "id": "effect"},
                        {"name": "Solution", "args": {"value": 2}, "id": "candidate"},
                    ],
                )
            ]
        ),
        tools=[action],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(
            verdict="verified", checks=[VerificationCheck(criterion_id="actual", passed=True)]
        ),
        limits=SolverLimits(max_tool_calls=0),
    )
    result = (
        await graph.ainvoke({"messages": [("user", "repair")]}, context=SolverContext(problem()))
    )["solver_result"]
    assert not result.verified
    assert result.verification.verdict == "inconclusive"
    assert effects == []
    assert result.usage.tool_calls == 0


async def test_verifier_receives_candidate_snapshot_without_copying_host_context():
    from pydantic import Field, Json

    from bm_workflow.agents import (
        SolverContext,
        VerificationCheck,
        VerificationReport,
        build_solver_agent,
    )

    class RichSolution(BaseModel):
        values: Json[list[int]] = Field(alias="result_values")

    host = object()
    proposals = []

    def verify(spec, proposal, context):
        assert context.host_context is host
        proposals.append(list(proposal.values))
        proposal.values.append("invalid")
        return VerificationReport(
            verdict="verified", checks=[VerificationCheck(criterion_id="actual", passed=True)]
        )

    graph = build_solver_agent(
        model=ScriptedModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "RichSolution", "args": {"result_values": "[1,2]"}, "id": "s"}
                    ],
                )
            ]
        ),
        tools=[],
        solution_schema=RichSolution,
        verifier=verify,
    )
    result = (
        await graph.ainvoke(
            {"messages": [("user", "repair")]},
            context=SolverContext(problem(), host_context=host),
        )
    )["solver_result"]
    assert result.verified
    assert result.candidate.values == [1, 2]
    assert proposals == [[1, 2], [1, 2]]


@pytest.mark.parametrize("verdict", ["blocked", "verified"])
async def test_terminal_no_candidate_can_be_host_blocked_but_never_verified(verdict):
    from bm_workflow.agents import (
        SolverContext,
        VerificationCheck,
        VerificationReport,
        build_solver_agent,
    )

    def verify(spec, proposal, context):
        assert proposal is None
        return VerificationReport(
            verdict=verdict,
            missing_requirements=["write capability"] if verdict == "blocked" else [],
            checks=[VerificationCheck(criterion_id="actual", passed=True)],
        )

    graph = build_solver_agent(
        model=ScriptedModel(responses=[AIMessage(content="I need a write capability to repair.")]),
        tools=[],
        solution_schema=Solution,
        verifier=verify,
    )
    result = (
        await graph.ainvoke({"messages": [("user", "repair")]}, context=SolverContext(problem()))
    )["solver_result"]
    assert result.verification.verdict == ("blocked" if verdict == "blocked" else "inconclusive")
    assert not result.verified
    assert result.candidate is None
    assert result.attempts == 0


@pytest.mark.parametrize("handled", [False, True])
async def test_only_host_declared_tool_errors_allow_recovery(handled):
    from langchain_core.tools import ToolException, tool

    from bm_workflow.agents import SolverContext, VerificationReport, build_solver_agent

    calls = []

    @tool
    def action() -> str:
        """Perform an effect that can fail."""
        calls.append(1)
        if handled:
            raise ToolException("Known safe failure; choose an alternative.")
        raise RuntimeError("Effect outcome is uncertain")

    action.handle_tool_error = handled
    graph = build_solver_agent(
        model=ScriptedModel(
            responses=[
                AIMessage(content="", tool_calls=[{"name": "action", "args": {}, "id": "a"}]),
                candidate(2),
            ]
        ),
        tools=[action],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(verdict="blocked"),
    )
    if handled:
        result = (
            await graph.ainvoke(
                {"messages": [("user", "repair")]}, context=SolverContext(problem())
            )
        )["solver_result"]
        assert result.verification.verdict == "blocked"
        assert result.usage.model_calls == 2
    else:
        with pytest.raises(RuntimeError, match="uncertain"):
            await graph.ainvoke(
                {"messages": [("user", "repair")]}, context=SolverContext(problem())
            )
    assert calls == [1]


@pytest.mark.parametrize("custom_specialist", [False, True])
async def test_default_specialist_inherits_parent_skill_sources(custom_specialist):
    from deepagents.backends.utils import create_file_data

    from bm_workflow.agents import SolverContext, VerificationReport, build_solver_agent

    class RecordingModel(ScriptedModel):
        _prompts: list[str] = PrivateAttr(default_factory=list)

        def _generate(self, messages, *args, **kwargs):
            self._prompts.append("\n".join(str(message.content) for message in messages))
            return super()._generate(messages, *args, **kwargs)

    marker = "Offline skill required to inspect the repaired value."
    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "inspect", "subagent_type": "general-purpose"},
                        "id": "delegate",
                    }
                ],
            ),
            AIMessage(content="inspected"),
            candidate(2),
        ]
    )
    graph = build_solver_agent(
        model=model,
        tools=[],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(verdict="inconclusive"),
        skills=["/skills/"],
        specialists=(
            [{"name": "general-purpose", "description": "Explicit isolated specialist"}]
            if custom_specialist
            else []
        ),
    )
    await graph.ainvoke(
        {
            "messages": [("user", "delegate inspection")],
            "files": {
                "/skills/repair/SKILL.md": create_file_data(
                    f"---\nname: repair\ndescription: {marker}\n---\nInspect actual state."
                )
            },
        },
        context=SolverContext(problem()),
    )
    assert len(model._prompts) == 3
    assert marker in model._prompts[0]
    assert (marker in model._prompts[1]) is (not custom_specialist)


@pytest.mark.parametrize("stop", ["deadline", "cancellation"])
async def test_default_specialist_skill_loading_obeys_shared_controls(stop):
    from deepagents.backends import StateBackend

    from bm_workflow.agents import (
        SolverContext,
        SolverLimits,
        VerificationReport,
        build_solver_agent,
    )
    from bm_workflow.errors import ExecutionError
    from bm_workflow.runtime import RunContext

    child_loading = asyncio.Event()
    child_cancelled = asyncio.Event()

    class SlowChildSkillsBackend(StateBackend):
        loads = 0

        async def als(self, path):
            self.loads += 1
            if self.loads == 2:
                child_loading.set()
                try:
                    await asyncio.sleep(10)
                finally:
                    child_cancelled.set()
            return await super().als(path)

    model = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "inspect", "subagent_type": "general-purpose"},
                        "id": "delegate",
                    }
                ],
            ),
            AIMessage(content="inspected"),
            candidate(2),
        ]
    )
    run = RunContext(name="skill-loading", version="1", definition_digest="d")
    context = SolverContext(problem(), workflow_context=run)
    graph = build_solver_agent(
        model=model,
        tools=[],
        solution_schema=Solution,
        verifier=lambda *args: VerificationReport(verdict="inconclusive"),
        skills=["/skills/"],
        backend=SlowChildSkillsBackend(),
        limits=SolverLimits(timeout_seconds=0.25 if stop == "deadline" else 10),
    )
    invocation = asyncio.create_task(
        graph.ainvoke({"messages": [("user", "delegate inspection")]}, context=context)
    )
    try:
        await asyncio.wait_for(child_loading.wait(), 1)
        if stop == "cancellation":
            run.cancellation.set()
            with pytest.raises(ExecutionError, match="cancelled"):
                await asyncio.wait_for(invocation, 1)
        else:
            output = await asyncio.wait_for(invocation, 1)
            assert not output["solver_result"].verified
        assert child_cancelled.is_set()
        assert model._index == 1
    finally:
        invocation.cancel()
        await asyncio.gather(invocation, return_exceptions=True)


@pytest.mark.parametrize("location", ["host", "specialist"])
@pytest.mark.parametrize("representation", ["callable", "tool", "openai", "flat", "schema"])
def test_task_tool_name_is_reserved_for_bounded_native_delegation(location, representation):
    from langchain_core.tools import tool

    from bm_workflow.agents import VerificationReport, build_solver_agent

    def task() -> str:
        """A host task tool must not shadow native bounded delegation."""
        pytest.fail("reserved tool executed")

    parameters = {"type": "object", "properties": {}}
    supplied = {
        "callable": task,
        "tool": tool(task),
        "openai": {"type": "function", "function": {"name": "task", "parameters": parameters}},
        "flat": {"type": "function", "name": "task", "parameters": parameters},
        "schema": {"title": "task", **parameters},
    }[representation]
    model = ScriptedModel(responses=[candidate(2)])
    with pytest.raises(ValueError, match=r"[Tt]ask|delegation"):
        build_solver_agent(
            model=model,
            tools=[supplied] if location == "host" else [],
            solution_schema=Solution,
            verifier=lambda *args: VerificationReport(verdict="inconclusive"),
            specialists=(
                [{"name": "inspector", "description": "Inspect", "tools": [supplied]}]
                if location == "specialist"
                else []
            ),
        )
    assert model._index == 0
