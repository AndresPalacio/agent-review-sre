from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from fin_graph.persistence.reference_app import (
    DuplicateNameError,
    FernetSecretCipher,
    JobStatus,
    RunStatus,
    open_reference_sqlite,
)


def _cipher() -> FernetSecretCipher:
    return FernetSecretCipher(Fernet.generate_key())


async def test_clean_initialization_and_restart_preserve_immutable_flow_revisions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "compat.sqlite"
    cipher = _cipher()

    async with open_reference_sqlite(database, cipher) as store:
        assert await store.list_flows() == ()
        flow, first_revision = await store.create_flow(
            "quality-flow",
            {"nodes": [{"id": "fundamentals", "kind": "analyst"}]},
            description="first",
        )
        updated, second_revision = await store.update_flow(
            flow.id,
            expected_version=flow.version,
            definition={"nodes": [{"id": "valuation", "kind": "analyst"}]},
            description="second",
        )

        assert first_revision.revision == 1
        assert second_revision.revision == 2
        assert updated.active_revision == 2
        assert first_revision.definition["nodes"] == [{"id": "fundamentals", "kind": "analyst"}]
        with pytest.raises(ValidationError):
            first_revision.revision = 9  # type: ignore[misc]

    async with open_reference_sqlite(database, cipher) as reopened:
        persisted = await reopened.get_flow(flow.id)
        revisions = await reopened.list_flow_revisions(flow.id)

    assert persisted is not None
    assert persisted.id == flow.id
    assert persisted.name == "quality-flow"
    assert persisted.version == 2
    assert [revision.revision for revision in revisions] == [1, 2]
    assert revisions[0].definition == first_revision.definition


async def test_duplicate_flow_names_are_rejected(tmp_path: Path) -> None:
    async with open_reference_sqlite(tmp_path / "compat.sqlite", _cipher()) as store:
        await store.create_flow("same-name", {"nodes": []})

        with pytest.raises(DuplicateNameError, match="same-name"):
            await store.create_flow("same-name", {"nodes": []})


async def test_runs_and_cycles_are_returned_in_creation_order(tmp_path: Path) -> None:
    async with open_reference_sqlite(tmp_path / "compat.sqlite", _cipher()) as store:
        flow, revision = await store.create_flow("ordered", {"nodes": []})
        run = await store.create_flow_run(
            flow.id,
            revision.revision,
            {"tickers": ["AAPL"]},
        )
        first = await store.append_run_cycle(run.id, "analyst.started", {"agent": "valuation"})
        second = await store.append_run_cycle(
            run.id,
            "analyst.completed",
            {"agent": "valuation"},
        )
        transitioned = await store.transition_flow_run(
            run.id,
            expected_version=run.version,
            status=RunStatus.SUCCEEDED,
            result={"decision": "hold"},
        )

        cycles = await store.list_run_cycles(run.id)
        runs = await store.list_flow_runs(flow.id)

    assert [cycle.id for cycle in cycles] == [first.id, second.id]
    assert [cycle.sequence for cycle in cycles] == [1, 2]
    assert transitioned is not None
    assert transitioned.status is RunStatus.SUCCEEDED
    assert transitioned.result == {"decision": "hold"}
    assert [item.id for item in runs] == [run.id]


async def test_concurrent_cycle_appends_receive_gapless_sequences(tmp_path: Path) -> None:
    async with open_reference_sqlite(tmp_path / "compat.sqlite", _cipher()) as store:
        flow, revision = await store.create_flow("concurrent-cycles", {"nodes": []})
        run = await store.create_flow_run(flow.id, revision.revision, {})

        created = await asyncio.gather(
            *(store.append_run_cycle(run.id, f"event-{index}", {}) for index in range(20))
        )
        persisted = await store.list_run_cycles(run.id)

    assert len(created) == 20
    assert [cycle.sequence for cycle in persisted] == list(range(1, 21))


async def test_provider_secrets_are_encrypted_and_never_returned_by_list(
    tmp_path: Path,
) -> None:
    database = tmp_path / "compat.sqlite"
    cipher = _cipher()
    secret = "provider-key-that-must-not-appear"

    async with open_reference_sqlite(database, cipher) as store:
        record = await store.put_provider_key("openai", secret)
        listed = await store.list_provider_keys()
        recovered = await store.get_provider_secret("openai")

    assert listed == (record,)
    assert all("secret" not in item.model_dump() for item in listed)
    assert recovered == secret
    assert secret.encode() not in database.read_bytes()

    async with open_reference_sqlite(database, cipher) as reopened:
        assert await reopened.get_provider_secret("openai") == secret


async def test_concurrent_provider_key_updates_increment_every_version(tmp_path: Path) -> None:
    secrets = [f"provider-secret-{index}" for index in range(10)]
    async with open_reference_sqlite(tmp_path / "compat.sqlite", _cipher()) as store:
        updated = await asyncio.gather(
            *(store.put_provider_key("openai", secret) for secret in secrets)
        )
        listed = await store.list_provider_keys()
        recovered = await store.get_provider_secret("openai")

    assert sorted(record.version for record in updated) == list(range(1, 11))
    assert listed[0].version == 10
    assert recovered in secrets


async def test_only_one_concurrent_backtest_transition_wins_compare_and_swap(
    tmp_path: Path,
) -> None:
    async with open_reference_sqlite(tmp_path / "compat.sqlite", _cipher()) as store:
        job = await store.create_backtest_job("alpha", {"ticker": "AAPL"})

        running, cancelled = await asyncio.gather(
            store.transition_backtest_job(
                job.id,
                expected_version=job.version,
                status=JobStatus.RUNNING,
            ),
            store.transition_backtest_job(
                job.id,
                expected_version=job.version,
                status=JobStatus.CANCELLED,
            ),
        )
        persisted = await store.get_backtest_job(job.id)

    winners = [result for result in (running, cancelled) if result is not None]
    assert len(winners) == 1
    assert persisted == winners[0]
    assert persisted is not None
    assert persisted.version == 2


async def test_artifacts_persist_with_stable_public_ids(tmp_path: Path) -> None:
    database = tmp_path / "compat.sqlite"
    cipher = _cipher()

    async with open_reference_sqlite(database, cipher) as store:
        job = await store.create_backtest_job("strategy", {"strategy": "momentum"})
        artifact = await store.create_artifact(
            job.id,
            "metrics",
            "artifacts/metrics.json",
            {"sha256": "a" * 64},
        )

    async with open_reference_sqlite(database, cipher) as reopened:
        artifacts = await reopened.list_artifacts(job.id)

    assert artifacts == (artifact,)
    assert artifact.id
    assert artifact.version == 1


async def test_invalid_text_fields_never_leave_corrupt_rows(tmp_path: Path) -> None:
    async with open_reference_sqlite(tmp_path / "compat.sqlite", _cipher()) as store:
        flow, revision = await store.create_flow("validated", {"nodes": []})
        run = await store.create_flow_run(flow.id, revision.revision, {})
        job = await store.create_backtest_job("valid", {})

        with pytest.raises((ValidationError, ValueError)):
            await store.append_run_cycle(run.id, "   ", {})
        with pytest.raises((ValidationError, ValueError)):
            await store.create_backtest_job("", {})
        with pytest.raises((ValidationError, ValueError)):
            await store.create_artifact(job.id, "", "artifacts/result.json", {})
        with pytest.raises((ValidationError, ValueError)):
            await store.create_artifact(job.id, "result", "   ", {})

        assert await store.list_run_cycles(run.id) == ()
        assert await store.list_backtest_jobs() == (job,)
        assert await store.list_artifacts(job.id) == ()
