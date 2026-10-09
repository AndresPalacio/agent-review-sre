from __future__ import annotations

import asyncio
from pathlib import Path

from sre_graph.persistence.action_ledger import open_sqlite_action_ledger


async def test_local_action_claim_is_atomic_across_two_process_connections(tmp_path: Path) -> None:
    path = tmp_path / "chat.sqlite"
    async with (
        open_sqlite_action_ledger(path) as first,
        open_sqlite_action_ledger(path) as second,
    ):
        results = await asyncio.gather(
            first.claim_action("thread:tool", "a" * 64),
            second.claim_action("thread:tool", "a" * 64),
        )
        assert sorted(status for status, _ in results) == ["new", "pending"]
