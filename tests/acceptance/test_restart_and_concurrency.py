from __future__ import annotations

import asyncio
from pathlib import Path

from fin_graph.compat.storage import ConcurrentUpdateError
from fin_graph.server.factory import RuntimeProfile, create_app


async def test_sqlite_restart_and_compare_and_swap_are_durable(tmp_path: Path) -> None:
    first = create_app(RuntimeProfile.local(data_dir=tmp_path))
    async with first.router.lifespan_context(first):
        store = first.state.compatibility_store
        flow, _revision = await store.create_flow(
            "acceptance-restart",
            {"nodes": [{"id": "valuation", "type": "agent-node"}]},
        )

        async def update(label: str) -> str:
            try:
                await store.update_flow(
                    flow.id,
                    expected_version=flow.version,
                    definition={"winner": label},
                )
            except ConcurrentUpdateError:
                return "conflict"
            return "updated"

        outcomes = await asyncio.gather(update("one"), update("two"))
        assert sorted(outcomes) == ["conflict", "updated"]

    restarted = create_app(RuntimeProfile.local(data_dir=tmp_path))
    async with restarted.router.lifespan_context(restarted):
        persisted = await restarted.state.compatibility_store.get_flow(flow.id)
        revisions = await restarted.state.compatibility_store.list_flow_revisions(flow.id)

    assert persisted is not None and persisted.version == 2
    assert [item.revision for item in revisions] == [1, 2]
