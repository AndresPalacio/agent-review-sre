"""Simulate registered external boundaries while traversing the actual graph."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from bm_workflow.compiler import CompiledWorkflow
from bm_workflow.runtime import (
    RunContext,
    RunResult,
    RuntimeEvent,
    _Scope,
    _scope,
    _sync,
    arun_workflow,
)


@dataclass(frozen=True)
class DryRunReport:
    result: RunResult
    events: tuple[RuntimeEvent, ...]


async def adry_run(
    compiled: CompiledWorkflow, input_data: Any, *, fixtures: Mapping[str, Any]
) -> DryRunReport:
    context = RunContext(
        compiled.definition.name, compiled.definition.version, compiled.definition_digest
    )
    scope = _Scope(context, fixtures=deepcopy(dict(fixtures)))
    token = _scope.set(scope)
    try:
        # LangGraph may otherwise inherit tracing enabled by the calling host.
        from langsmith import tracing_context

        with tracing_context(enabled=False):
            result = await arun_workflow(compiled, input_data, context=context)
        return DryRunReport(result, tuple(scope.events))
    finally:
        _scope.reset(token)


def dry_run(
    compiled: CompiledWorkflow, input_data: Any, *, fixtures: Mapping[str, Any]
) -> DryRunReport:
    return _sync(lambda: adry_run(compiled, input_data, fixtures=fixtures))
