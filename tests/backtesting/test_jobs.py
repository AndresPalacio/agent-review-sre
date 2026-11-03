from __future__ import annotations

import asyncio
from pathlib import Path

from cryptography.fernet import Fernet

from fin_graph.backtesting.artifacts import BacktestArtifactStore, BacktestRunOutput
from fin_graph.backtesting.jobs import BacktestJobManager
from fin_graph.persistence.reference_app import FernetSecretCipher, JobStatus, open_reference_sqlite


def _cipher() -> FernetSecretCipher:
    return FernetSecretCipher(Fernet.generate_key())


async def _wait_for(manager: BacktestJobManager, job_id: str, status: JobStatus):
    for _ in range(200):
        record = await manager.get(job_id)
        if record is not None and record.status is status:
            return record
        await asyncio.sleep(0.005)
    raise AssertionError(f"job {job_id} never reached {status}")


async def test_jobs_bound_concurrency_publish_success_and_page_signals(tmp_path: Path) -> None:
    active = 0
    maximum = 0
    release = asyncio.Event()

    async def runner(request, *, event_sink, cancellation):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await release.wait()
            return BacktestRunOutput(
                result={"name": request["name"], "ok": True},
                signals=tuple({"index": index} for index in range(5)),
            )
        finally:
            active -= 1

    async with open_reference_sqlite(tmp_path / "jobs.sqlite", _cipher()) as store:
        manager = BacktestJobManager(
            store,
            BacktestArtifactStore(tmp_path / "artifacts"),
            {"alpha": runner},
            max_workers=2,
        )
        jobs = [await manager.start("alpha", {"name": str(index)}) for index in range(4)]
        for _ in range(100):
            if maximum == 2:
                break
            await asyncio.sleep(0.005)
        assert maximum == 2
        release.set()
        records = [await _wait_for(manager, job.id, JobStatus.SUCCEEDED) for job in jobs]
        await manager.close()

        page = await manager.signals(jobs[0].id, offset=1, limit=2)
        replay = await manager.events(jobs[0].id)
        artifacts = await store.list_artifacts(jobs[0].id)

    assert all(
        record.result == {"name": str(index), "ok": True} for index, record in enumerate(records)
    )
    assert [item["index"] for item in page.items] == [1, 2]
    assert page.total == 5
    assert page.next_offset == 3
    assert [event.sequence for event in replay] == list(range(len(replay)))
    assert sum(event.terminal for event in replay) == 1
    assert {artifact.kind for artifact in artifacts} == {"result", "signals"}


async def test_cancel_wins_race_and_never_publishes_partial_success(tmp_path: Path) -> None:
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def slow_runner(request, *, event_sink, cancellation):
        entered.set()
        await finish.wait()
        return BacktestRunOutput(result={"should": "not-publish"}, signals=({"x": 1},))

    async with open_reference_sqlite(tmp_path / "cancel.sqlite", _cipher()) as store:
        artifacts = BacktestArtifactStore(tmp_path / "artifacts")
        manager = BacktestJobManager(store, artifacts, {"alpha": slow_runner})
        job = await manager.start("alpha", {})
        await entered.wait()
        await manager.cancel(job.id)
        finish.set()
        record = await _wait_for(manager, job.id, JobStatus.CANCELLED)
        await manager.close()

        events = await manager.events(job.id)
        published = await store.list_artifacts(job.id)

    assert record.result is None
    assert published == ()
    assert not artifacts.success_directory(job.id).exists()
    assert sum(event.terminal for event in events) == 1


async def test_failure_details_are_persisted_with_one_terminal_event(tmp_path: Path) -> None:
    async def broken(request, *, event_sink, cancellation):
        raise RuntimeError("provider exploded")

    async with open_reference_sqlite(tmp_path / "failed.sqlite", _cipher()) as store:
        manager = BacktestJobManager(
            store, BacktestArtifactStore(tmp_path / "artifacts"), {"alpha": broken}
        )
        job = await manager.start("alpha", {})
        record = await _wait_for(manager, job.id, JobStatus.FAILED)
        await manager.close()
        events = await manager.events(job.id)

    assert record.error is not None and "provider exploded" in record.error
    assert sum(event.terminal for event in events) == 1
    assert events[-1].message == "Backtest failed: RuntimeError: provider exploded"


async def test_restart_recovers_persisted_queued_job_and_replays_events(tmp_path: Path) -> None:
    database = tmp_path / "restart.sqlite"
    artifact_root = tmp_path / "artifacts"

    async def runner(request, *, event_sink, cancellation):
        return BacktestRunOutput(result={"recovered": request["value"]}, signals=())

    async with open_reference_sqlite(database, _cipher()) as store:
        queued = await store.create_backtest_job("alpha", {"value": 7})

    async with open_reference_sqlite(database, _cipher()) as reopened:
        manager = BacktestJobManager(
            reopened, BacktestArtifactStore(artifact_root), {"alpha": runner}
        )
        recovered = await manager.recover()
        record = await _wait_for(manager, queued.id, JobStatus.SUCCEEDED)
        await manager.close()

    async with open_reference_sqlite(database, _cipher()) as final_store:
        replay_manager = BacktestJobManager(
            final_store, BacktestArtifactStore(artifact_root), {"alpha": runner}
        )
        events = await replay_manager.events(queued.id)

    assert recovered == (queued.id,)
    assert record.result == {"recovered": 7}
    assert events[-1].terminal
