from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.compat.storage import FernetSecretCipher, RunStatus
from fin_graph.compat.storage.postgres import open_compat_postgres
from fin_graph.server.migrate import upgrade


@pytest.mark.postgres
async def test_postgres_store_persists_and_enforces_compare_and_swap(postgres_url: str) -> None:
    await asyncio.to_thread(upgrade, postgres_url)
    engine = create_async_engine(postgres_url)
    cipher = FernetSecretCipher(Fernet.generate_key())
    try:
        async with open_compat_postgres(engine, cipher=cipher) as store:
            flow, revision = await store.create_flow("postgres-compat", {"nodes": []})
            run = await store.create_flow_run(flow.id, revision.revision, {})
            first, second = await asyncio.gather(
                store.transition_flow_run(
                    run.id,
                    expected_version=run.version,
                    status=RunStatus.RUNNING,
                ),
                store.transition_flow_run(
                    run.id,
                    expected_version=run.version,
                    status=RunStatus.CANCELLED,
                ),
            )
            assert sum(item is not None for item in (first, second)) == 1
    finally:
        await engine.dispose()


@pytest.mark.postgres
async def test_postgres_cycle_sequence_is_gapless_across_store_instances(
    postgres_url: str,
) -> None:
    await asyncio.to_thread(upgrade, postgres_url)
    first_engine = create_async_engine(postgres_url)
    second_engine = create_async_engine(postgres_url)
    cipher = FernetSecretCipher(Fernet.generate_key())
    try:
        async with (
            open_compat_postgres(first_engine, cipher=cipher) as first,
            open_compat_postgres(second_engine, cipher=cipher) as second,
        ):
            flow, revision = await first.create_flow("postgres-cycles", {"nodes": []})
            run = await first.create_flow_run(flow.id, revision.revision, {})
            await asyncio.gather(
                *(
                    store.append_run_cycle(run.id, f"event-{index}", {})
                    for index, store in enumerate((first, second) * 10)
                )
            )
            cycles = await first.list_run_cycles(run.id)
            assert [item.sequence for item in cycles] == list(range(1, 21))
    finally:
        await first_engine.dispose()
        await second_engine.dispose()
