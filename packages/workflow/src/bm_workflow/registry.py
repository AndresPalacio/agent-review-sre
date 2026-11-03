"""Exact-version lookup and release identity without mutable global registries."""

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from bm_workflow.compiler import CompiledWorkflow
from bm_workflow.errors import DefinitionError, WorkflowNotFoundError
from bm_workflow.runtime import RunContext, RunResult, arun_workflow, run_workflow


class WorkflowRegistry:
    def __init__(self, workflows: Iterable[CompiledWorkflow]) -> None:
        entries = {}
        for item in workflows:
            key = (item.definition.name, item.definition.version)
            if key in entries:
                raise DefinitionError("Duplicate workflow name/version")
            entries[key] = item
        self._entries = MappingProxyType(entries)

    def resolve(self, name: str, version: str) -> CompiledWorkflow:
        try:
            return self._entries[(name, version)]
        except KeyError:
            raise WorkflowNotFoundError("Workflow version is not registered") from None

    def manifest(self) -> list[dict[str, str]]:
        return [
            {"name": name, "version": version, "definition_digest": item.definition_digest}
            for (name, version), item in sorted(self._entries.items())
        ]

    def release_manifest(
        self, *, previous: Iterable[Mapping[str, str]] = ()
    ) -> list[dict[str, str]]:
        history: dict[tuple[str, str], dict[str, str]] = {}
        for row in [*previous, *self.manifest()]:
            key = (row["name"], row["version"])
            entry = dict(row)
            if key in history and history[key]["definition_digest"] != entry["definition_digest"]:
                raise DefinitionError("A published workflow version cannot change digest")
            history[key] = entry
        return [history[key] for key in sorted(history)]

    def run(
        self, *, name: str, version: str, input_data: Any, context: RunContext | None = None
    ) -> RunResult:
        return run_workflow(self.resolve(name, version), input_data, context=context)

    async def arun(
        self, *, name: str, version: str, input_data: Any, context: RunContext | None = None
    ) -> RunResult:
        return await arun_workflow(self.resolve(name, version), input_data, context=context)
