from __future__ import annotations

import pytest

from fin_graph.adapters.reference_app import python_api, runtime
from fin_graph.integrations.context import RunContext


async def test_async_python_api_closes_runtime_context_after_graph_failure(monkeypatch) -> None:
    class CloseableClient:
        def __init__(self) -> None:
            self.closes = 0

        async def aclose(self) -> None:
            self.closes += 1

    class FailingGraph:
        async def ainvoke(self, _value):
            raise RuntimeError("graph failed")

    closeable = CloseableClient()
    context = RunContext(owned_clients=(closeable,))

    async def make_runtime():
        return FailingGraph(), context

    monkeypatch.setattr(runtime, "make_reference_analysis_runtime_async", make_runtime)

    with pytest.raises(RuntimeError, match="graph failed"):
        await python_api.run_hedge_fund_async(
            ["AAPL"],
            "2024-01-01",
            "2024-01-31",
            {"cash": 100_000},
        )

    assert closeable.closes == 1
