"""Distributed compatibility store over the server's Postgres engine."""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from fin_graph.compat.contracts import JsonObject
from fin_graph.compat.storage.crypto import FernetSecretCipher, SecretCipher
from fin_graph.compat.storage.models import ProviderKeyRecord, RunCycleRecord
from fin_graph.compat.storage.protocol import CompatibilityStore
from fin_graph.compat.storage.sqlite import SqliteCompatibilityStore


def _lock_key(namespace: str, identity: str) -> int:
    return int.from_bytes(
        hashlib.blake2b(f"{namespace}:{identity}".encode(), digest_size=8).digest(),
        "big",
        signed=True,
    )


class PostgresCompatibilityStore(SqliteCompatibilityStore):
    """SQL implementation plus cross-process locks for generated sequences/upserts."""

    @asynccontextmanager
    async def _advisory_lock(self, namespace: str, identity: str) -> AsyncIterator[None]:
        key = _lock_key(namespace, identity)
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
            try:
                yield
            finally:
                await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                await connection.commit()

    async def append_run_cycle(
        self, run_id: str, event: str, payload: JsonObject
    ) -> RunCycleRecord:
        async with self._advisory_lock("compat-cycle", run_id):
            return await super().append_run_cycle(run_id, event, payload)

    async def put_provider_key(
        self,
        provider: str,
        secret: str,
        *,
        description: str | None = None,
        is_active: bool = True,
    ) -> ProviderKeyRecord:
        async with self._advisory_lock("compat-provider", provider.strip().lower()):
            return await super().put_provider_key(
                provider,
                secret,
                description=description,
                is_active=is_active,
            )


def _environment_cipher() -> SecretCipher:
    value = os.getenv("FIN_GRAPH_COMPATIBILITY_KEY", "").strip()
    if not value:
        raise ValueError(
            "FIN_GRAPH_COMPATIBILITY_KEY is required when no compatibility cipher is injected"
        )
    return FernetSecretCipher(value)


@asynccontextmanager
async def _open_compat_postgres(
    engine: AsyncEngine, cipher: SecretCipher
) -> AsyncIterator[CompatibilityStore]:
    if engine.dialect.name != "postgresql":
        raise ValueError("Postgres compatibility store requires a Postgres engine")
    yield PostgresCompatibilityStore(engine, cipher)


def open_compat_postgres(
    pool: AsyncEngine,
    *,
    cipher: SecretCipher | None = None,
) -> AbstractAsyncContextManager[CompatibilityStore]:
    """Open a compatibility store over an owned server pool/engine.

    The caller retains ownership of the engine so the native and compatibility
    ledgers share one bounded deployment pool without double disposal.
    """

    return _open_compat_postgres(pool, cipher or _environment_cipher())


__all__ = ["PostgresCompatibilityStore", "open_compat_postgres"]
