"""DeepAgent factory for an optimizer workspace bounded to one directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend


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
