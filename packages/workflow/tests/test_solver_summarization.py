"""Native compaction must retain recovery without escaping shared quotas."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, PrivateAttr

pytest.importorskip("deepagents")
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import ContextOverflowError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from bm_workflow.agents import (
    ProblemSpec,
    SolverContext,
    SolverLimits,
    VerificationCheck,
    VerificationReport,
    build_solver_agent,
)


class SummarySolution(BaseModel):
    value: int


class OverflowSummaryModel(BaseChatModel):
    """Provider boundary double: reject the first summary, record real requests."""

    _summary_prompts: list[str] = PrivateAttr(default_factory=list)
    _agent_calls: int = PrivateAttr(default=0)

    @property
    def _llm_type(self):
        return "offline-summary-overflow"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        first = str(messages[0].content)
        is_summary = (
            "Context Extraction Assistant" in first
            or "Summarize the following conversation" in first
        )
        if is_summary:
            self._summary_prompts.append("\n".join(str(message.content) for message in messages))
            if len(self._summary_prompts) == 1:
                raise ContextOverflowError("Local summary request exceeds available context")
            message = AIMessage(
                content="Goal: inspect /media/chart.png; actual value remains 2.",
                usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            )
        else:
            self._agent_calls += 1
            message = AIMessage(
                content="",
                tool_calls=[{"name": "SummarySolution", "args": {"value": 2}, "id": "finish"}],
                usage_metadata={"input_tokens": 13, "output_tokens": 5, "total_tokens": 18},
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


class ModelCalls(BaseCallbackHandler):
    def __init__(self):
        self.started = 0

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.started += 1


@pytest.mark.parametrize("summary_allowance", [1, 2])
async def test_native_summary_overflow_recovery_respects_each_attempt(summary_allowance):
    # Returning a raw Runnable summarizer loses native overflow recovery; counting
    # only the logical summary instead would let the allowance-one run overspend.
    model = OverflowSummaryModel(profile={"max_input_tokens": 16000})
    callbacks = ModelCalls()
    problem = ProblemSpec(
        id="summary-recovery",
        goal="Inspect /media/chart.png and report actual value 2",
        acceptance_criteria={"actual": "Actual value is 2"},
    )
    graph = build_solver_agent(
        model=model,
        tools=[],
        solution_schema=SummarySolution,
        verifier=lambda *args: VerificationReport(
            verdict="verified", checks=[VerificationCheck(criterion_id="actual", passed=True)]
        ),
        limits=SolverLimits(max_summary_calls=summary_allowance, timeout_seconds=10),
    )
    history = [("user", '<image path="/media/chart.png" /> Goal: inspect the chart.')]
    history += [("user", "old history " * 1000), ("assistant", "old response")] * 14
    history.append(("user", "finish"))
    output = await graph.ainvoke(
        {"messages": history},
        config={"callbacks": [callbacks]},
        context=SolverContext(problem=problem),
    )
    result = output["solver_result"]
    assert len(model._summary_prompts) == summary_allowance
    assert result.usage.summary_calls == summary_allowance
    # The native prompt supplies artifact/media preservation instructions and
    # transcript serialization; these requests are the actual provider boundary.
    assert "media_reference_information" in model._summary_prompts[0]
    assert "SESSION INTENT" in model._summary_prompts[0]
    assert "/media/chart.png" in model._summary_prompts[0]
    if summary_allowance == 1:
        assert not result.verified
        assert result.usage.model_calls == model._agent_calls == 0
        assert callbacks.started == 1
    else:
        assert result.verified
        assert len(model._summary_prompts[1]) < len(model._summary_prompts[0])
        assert result.usage.model_calls == model._agent_calls == 1
        assert (result.usage.input_tokens, result.usage.output_tokens) == (24, 12)
        assert callbacks.started == 3
