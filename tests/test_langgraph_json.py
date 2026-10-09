from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from langsmith.utils import tracing_is_enabled

ROOT = Path(__file__).resolve().parents[1]


def _graph_targets() -> dict[str, str]:
    config = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))
    graphs = config["graphs"]
    assert isinstance(graphs, dict)
    return graphs


def _module_name(path: str) -> str:
    relative = Path(path).as_posix().removeprefix("./").removeprefix("src/")
    return relative.removesuffix(".py").replace("/", ".")


async def _build(graph_id: str) -> Any:
    path, attribute = _graph_targets()[graph_id].rsplit(":", 1)
    result = getattr(importlib.import_module(_module_name(path)), attribute)()
    return await result if inspect.isawaitable(result) else result


@pytest.mark.parametrize("graph_id", sorted(_graph_targets()))
async def test_studio_graph_compiles_without_checkpointer(
    graph_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    graph = await _build(graph_id)

    assert graph.checkpointer is None
    assert graph.get_graph().nodes


@pytest.mark.parametrize("graph_id", sorted(_graph_targets()))
async def test_building_a_studio_graph_turns_external_tracing_off(
    graph_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tracing_enabled_by_environment: None,
) -> None:
    monkeypatch.chdir(tmp_path)

    await _build(graph_id)

    assert not tracing_is_enabled()
