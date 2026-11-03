"""DeepAgent factory for an optimizer workspace bounded to one directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

from fin_graph.agents.catalog import AgentDescriptor


def make_optimizer_agent(
    *,
    model: Any,
    system_prompt: str,
    workspace_root: Path,
) -> Any:
    """Build the optional proposer with access only to its virtual workspace."""

    return create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        backend=FilesystemBackend(root_dir=str(workspace_root), virtual_mode=True),
    )


OPTIMIZER_AGENT_DESCRIPTORS = (
    AgentDescriptor(
        id="optimizer.proposer",
        display_name="Optimization proposer",
        kind="deepagents_supervisor",
        purpose="Propose bounded text edits inside one isolated optimization workspace.",
        factory_path="fin_graph.agents.optimizer.proposer:make_optimizer_agent",
        tool_policy="workspace",
        limits=("virtual workspace root", "outer iteration timeout", "editable manifest"),
        activation="Only when the optional optimization harness runs.",
        surfaces=("fin-graph harness run",),
    ),
)
