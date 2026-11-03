"""Offline native task delegation with a worker, read-only inspector and host checks.

From packages/workflow: uv run --extra agents python examples/solver_team.py
All models are local scripted contract fixtures. No provider calls are made.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langsmith import tracing_context
from pydantic import PrivateAttr

from bm_workflow.agents import SolverContext, SolverResult, build_solver_agent

if __package__:
    from .solver_benchmark import LocalEnvironment, Solution, cases, grade_environment
else:
    from solver_benchmark import LocalEnvironment, Solution, cases, grade_environment


class TeamModel(BaseChatModel):
    """A deterministic role policy inside the real native delegation graph."""

    role: Literal["parent", "worker", "inspector"]
    _turn: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "offline-native-team-contract"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        turn = self._turn
        self._turn += 1

        def call(name: str, args: dict[str, Any]) -> AIMessage:
            return AIMessage(
                content="",
                tool_calls=[{"name": name, "args": args, "id": f"{self.role}-{turn}"}],
            )

        if self.role == "parent":
            if turn == 0:
                message = call(
                    "task",
                    {
                        "subagent_type": "general-purpose",
                        "description": "Repair config: port 8443, retries 3; preserve log_level.",
                    },
                )
            elif turn == 1:
                message = call(
                    "task",
                    {
                        "subagent_type": "verifier",
                        "description": "Read and inspect config/service.json without modifying it.",
                    },
                )
            else:
                message = call(
                    "Solution",
                    {
                        "resolved": True,
                        "summary": "Worker repaired config; inspector read the resulting values.",
                        "evidence_refs": ["config/service.json"],
                    },
                )
        elif turn == 0:
            message = call("read_document", {"path": "config/service.json"})
        elif self.role == "worker" and turn == 1:
            observed = next(item for item in reversed(messages) if isinstance(item, ToolMessage))
            config = json.loads(json.loads(str(observed.content))["content"])
            config.update(port=8443, retries=3)
            message = call(
                "write_document",
                {
                    "path": "config/service.json",
                    "content": json.dumps(config),
                },
            )
        else:
            observed = next(item for item in reversed(messages) if isinstance(item, ToolMessage))
            message = AIMessage(content=f"Local tool result: {observed.content}")
        return ChatResult(generations=[ChatGeneration(message=message)])


async def run_team() -> tuple[SolverResult[Solution], LocalEnvironment, tuple[str, ...]]:
    case = next(case for case in cases() if case.id == "config-direct")
    environment = LocalEnvironment(case)
    tools = environment.tools()
    read_tool = next(tool for tool in tools if tool.name == "read_document")
    read_tool.metadata = {"read_only": True}
    problem = case.problem()
    # The host retains graph/model/backend lifecycle; no compiled graph is cached.
    graph = build_solver_agent(
        model=TeamModel(role="parent"),
        tools=tools,
        solution_schema=Solution,
        verifier=environment.verify,
        limits=case.limits(),
        specialists=(
            {
                "name": "general-purpose",
                "description": "Repair this task's local configuration using scoped host tools.",
                "system_prompt": "Read config, repair requested values and report tool results.",
                "model": TeamModel(role="worker"),
                "tools": tools,
            },
            {
                "name": "verifier",
                "description": "Read-only inspection of resulting local documents.",
                "system_prompt": "Inspect actual values. Never modify documents or claim approval.",
                "model": TeamModel(role="inspector"),
                "tools": [read_tool],
            },
        ),
    )
    with tracing_context(enabled=False):
        output = await graph.ainvoke(
            {"messages": [HumanMessage(problem.model_dump_json())]},
            context=SolverContext(problem=problem, host_context=environment),
        )
    completed = {
        message.tool_call_id
        for message in output["messages"]
        if isinstance(message, ToolMessage)
        and message.name == "task"
        and message.status == "success"
    }
    delegations = tuple(
        call["args"]["subagent_type"]
        for message in output["messages"]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call["id"] in completed
    )
    return output["solver_result"], environment, delegations


async def main() -> None:
    result, environment, delegations = await run_team()
    case = next(case for case in cases() if case.id == "config-direct")
    grade = grade_environment(case, environment)
    print(
        json.dumps(
            {
                "mode": "scripted_contract",
                "delegations": delegations,
                "verified": result.verified,
                "independent_grade": grade.passed,
                "result": result.model_dump(mode="json"),
                "documents": environment.documents,
            },
            indent=2,
        )
    )
    if (
        not result.verified
        or not grade.passed
        or set(delegations) != {"general-purpose", "verifier"}
    ):
        raise SystemExit("Native team did not produce a verified repair with both specialists.")


if __name__ == "__main__":
    asyncio.run(main())
