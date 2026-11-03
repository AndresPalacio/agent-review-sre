from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from bm_workflow.agents import ProblemSpec, SolverContext, SolverLimits, solver_step


@pytest.mark.parametrize("construction", ["construct", "copy"])
def test_context_revalidates_unchecked_problem_instances(construction):
    values = dict(id="test", goal="repair", acceptance_criteria={})
    if construction == "construct":
        spec = ProblemSpec.model_construct(**values)
    else:
        spec = ProblemSpec(id="test", goal="repair", acceptance_criteria={"c": "check"})
        spec = spec.model_copy(update={"acceptance_criteria": {}})
    with pytest.raises(ValidationError):
        SolverContext(spec)


def test_context_snapshots_and_freezes_unchecked_problem_containers():
    criteria = {"actual": "Original criterion"}
    inputs = {"nested": [1]}
    spec = ProblemSpec.model_construct(
        id="test", goal="repair", inputs=inputs, acceptance_criteria=criteria
    )
    context = SolverContext(spec)
    criteria["actual"] = "Weakened criterion"
    inputs["nested"].append(2)
    assert context.problem.acceptance_criteria == {"actual": "Original criterion"}
    assert context.problem.inputs["nested"] == (1,)
    with pytest.raises(TypeError):
        context.problem.acceptance_criteria["actual"] = "Changed"


async def test_solver_step_rejects_unchecked_problem_before_graph_invocation():
    class Solution(BaseModel):
        value: int

    async def forbidden(*args, **kwargs):
        pytest.fail("invalid problem reached graph")

    node = solver_step(
        "solve",
        forbidden,
        solution_schema=Solution,
        output_key="answer",
        problem_mapper=lambda state: ProblemSpec.model_construct(
            id="test", goal="repair", acceptance_criteria={}
        ),
    )
    with pytest.raises(ValidationError):
        await node.handler({})


def test_context_start_revalidates_unchecked_limits():
    context = SolverContext(
        ProblemSpec(id="test", goal="repair", acceptance_criteria={"actual": "check"})
    )
    with pytest.raises(ValidationError):
        context.start(SolverLimits().model_copy(update={"max_repairs": 3}), None)


def test_builder_rejects_unchecked_limits():
    pytest.importorskip("deepagents")
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from bm_workflow.agents import build_solver_agent

    class Solution(BaseModel):
        value: int

    with pytest.raises(ValidationError):
        build_solver_agent(
            model=FakeMessagesListChatModel(responses=[]),
            tools=[],
            solution_schema=Solution,
            verifier=lambda *args: None,
            limits=SolverLimits().model_copy(update={"max_repairs": 3}),
        )
