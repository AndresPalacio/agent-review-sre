from __future__ import annotations

from typing import Any

import pytest

from fin_graph.harness import runtime
from fin_graph.integrations.registry import IntegrationRegistry


@pytest.mark.parametrize("studio", [False, True])
async def test_runtime_without_model_still_reports_unknown(
    monkeypatch: pytest.MonkeyPatch, studio: bool
) -> None:
    monkeypatch.setattr(runtime, "default_registry", lambda: IntegrationRegistry([], env_file=None))

    def missing_model(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("provider secret must not escape")

    monkeypatch.setattr(runtime, "get_model", missing_model)
    if studio:
        graph = runtime.make_runtime_graph()
        context = None
    else:
        graph, context = runtime.make_runtime()
    result = await graph.ainvoke({"raw_alert": {"title": "Checkout error"}}, context=context)
    assert result["diagnosis"]["category"] == "unknown"
    assert result["harness"]["mode"] == "incident"
    assert "provider secret" not in str(result)


def test_sync_cli_discovers_configured_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    native = IntegrationRegistry([], env_file=None)
    extended = IntegrationRegistry([], env_file=None)
    calls = []
    monkeypatch.setattr(runtime, "default_registry", lambda: native)
    monkeypatch.setattr(runtime, "configured_mcp_path", lambda registry: "mcp.json")

    async def discover(registry):
        calls.append(registry)
        return extended

    monkeypatch.setattr(runtime, "with_configured_mcp", discover)
    runtime.make_runtime()
    assert calls == [native]


async def test_async_studio_discovers_mcp_without_nested_event_loop(monkeypatch) -> None:
    native = IntegrationRegistry([], env_file=None)
    calls = []
    monkeypatch.setattr(runtime, "default_registry", lambda: native)

    async def discover(registry):
        calls.append(registry)
        return registry

    monkeypatch.setattr(runtime, "with_configured_mcp", discover)
    graph = await runtime.make_runtime_graph_async()
    assert graph.checkpointer is None
    assert calls == [native]
