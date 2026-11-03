"""Verified native solvers with optional DeepAgents dependencies."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from pydantic import BaseModel

from bm_workflow.adapters import (
    _agent_invocation,
    _agent_metadata,
    _invoke,
    _metadata,
    _output_contract,
)
from bm_workflow.agent_types import AgentInvocation, AgentManifest, InvocationFactory
from bm_workflow.definition import StepNode, step, validate_model
from bm_workflow.runtime import call_handler, current_context

from .contracts import (
    ProblemSpec,
    SolverContext,
    SolverLimits,
    SolverResult,
    SolverUsage,
    VerificationCheck,
    VerificationReport,
)

__all__ = [
    "ProblemSpec",
    "SolverContext",
    "SolverLimits",
    "SolverResult",
    "SolverUsage",
    "VerificationCheck",
    "VerificationReport",
    "build_solver_agent",
    "solver_step",
]


def build_solver_agent(
    *,
    model: Any,
    tools: Sequence[Any],
    solution_schema: type[BaseModel],
    verifier: Callable[..., Any],
    limits: SolverLimits | None = None,
    specialists: Sequence[Mapping[str, Any]] = (),
    skills: list[str] | None = None,
    backend: Any = None,
    observer: Callable[[Mapping[str, Any]], None] | None = None,
) -> Any:
    """Build once in the host lifespan; invoke asynchronously with a fresh SolverContext.

    Tools are never retried automatically. Reuse EffectCoordinator in host tools
    for idempotent external effects. Native summaries have separate reservations.
    """
    try:
        from .native import build_native
    except ImportError as exc:
        raise ImportError("build_solver_agent requires bm-workflow[agents]") from exc
    return build_native(
        model=model,
        tools=tools,
        solution_schema=solution_schema,
        verifier=verifier,
        limits=cast(SolverLimits, validate_model(SolverLimits, limits or SolverLimits())),
        specialists=specialists,
        skills=skills,
        backend=backend,
        observer=observer,
    )


def solver_step(
    id: str,
    graph: Any,
    *,
    solution_schema: type[BaseModel],
    output_key: str,
    problem_mapper: Callable[[dict[str, Any]], Any] | None = None,
    invocation_factory: InvocationFactory | None = None,
    manifest: AgentManifest | None = None,
    artifact_version: str = "1",
) -> StepNode:
    """External step: dry-run bypasses mapping, factory, graph, tools and verifier."""
    schema = SolverResult[solution_schema]  # type: ignore[valid-type]

    async def handler(state: dict[str, Any]) -> dict[str, Any]:
        value = await call_handler(problem_mapper, state) if problem_mapper else state
        problem = cast(ProblemSpec, validate_model(ProblemSpec, value))
        invocation = await _agent_invocation(invocation_factory, state)
        context = SolverContext(
            problem=problem,
            host_context=invocation.context if invocation else None,
            workflow_context=current_context(),
        )
        bound = AgentInvocation(config=invocation.config if invocation else {}, context=context)
        output = await _invoke(
            graph, {"messages": [("user", "Solve the authoritative host problem.")]}, bound
        )
        if not isinstance(output, Mapping) or output.get("__interrupt__"):
            raise ValueError("Solver graph pauses are unsupported")
        if "solver_result" not in output:
            raise ValueError("Solver graph must return solver_result")
        result = validate_model(schema, output["solver_result"])
        return {output_key: result.model_dump(mode="python", round_trip=True)}

    return step(
        id,
        handler,
        kind="external",
        artifact_version=artifact_version,
        output_schema=_output_contract(schema, output_key),
        metadata=_metadata("solver", schema, output_key, **_agent_metadata(manifest)),
    )
