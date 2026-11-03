from __future__ import annotations

from pathlib import Path
from typing import Any


def test_optimizer_factory_forwards_only_bounded_workspace(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    import fin_graph.agents.optimizer.proposer as module

    captured: dict[str, object] = {}
    model = object()
    sentinel = object()
    monkeypatch.setattr(module, "FilesystemBackend", lambda **kwargs: ("backend", kwargs))
    monkeypatch.setattr(
        module,
        "create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or sentinel,
    )

    graph = module.make_optimizer_agent(
        model=model,
        system_prompt="bounded",
        workspace_root=tmp_path,
    )

    assert graph is sentinel
    assert captured == {
        "model": model,
        "system_prompt": "bounded",
        "backend": ("backend", {"root_dir": str(tmp_path), "virtual_mode": True}),
    }
