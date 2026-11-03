from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from bm_workflow.persistence import (
    InMemoryRunStore,
    PersistenceError,
    RunRecord,
    RunService,
    RunState,
    SQLiteRunStore,
)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    value = (
        InMemoryRunStore() if request.param == "memory" else SQLiteRunStore(tmp_path / "runs.db")
    )
    yield value
    if hasattr(value, "close"):
        value.close()


def record(**changes):
    return RunRecord(
        run_id="run-1",
        name="example",
        workflow_version="1",
        definition_digest="digest",
        artifact_revision="rev",
        input_data={"items": [1]},
        **changes,
    )


def test_store_create_and_snapshots_are_detached_and_namespaced(store):
    source = record()
    assert store.create("a", source)
    assert not store.create("a", source)
    assert store.get("b", source.run_id) is None
    source.input_data["items"].append(2)
    snapshot = store.get("a", source.run_id)
    assert snapshot.input_data == {"items": [1]}
    snapshot.input_data["items"].append(3)
    assert store.get("a", source.run_id).input_data == {"items": [1]}


def test_compare_and_swap_enforces_versions_and_legal_transitions(store):
    initial = record()
    assert store.create("a", initial)
    running = initial.model_copy(update={"state": RunState.RUNNING, "version": 1})
    assert store.compare_and_swap("a", initial.run_id, 0, running)
    assert not store.compare_and_swap("a", initial.run_id, 0, running)
    with pytest.raises(PersistenceError, match="invalid_transition"):
        store.compare_and_swap(
            "a",
            initial.run_id,
            1,
            running.model_copy(update={"state": RunState.ACCEPTED, "version": 2}),
        )


def test_only_one_concurrent_connection_claims_a_run(tmp_path):
    path = tmp_path / "runs.db"
    with SQLiteRunStore(path) as first, SQLiteRunStore(path) as second:
        initial = record()
        first.create("a", initial)
        running = initial.model_copy(update={"state": RunState.RUNNING, "version": 1})
        with ThreadPoolExecutor() as pool:
            results = list(
                pool.map(
                    lambda store: store.compare_and_swap("a", "run-1", 0, running), [first, second]
                )
            )
        assert sorted(results) == [False, True]
    with SQLiteRunStore(path) as reopened:
        assert reopened.get("a", "run-1").state == RunState.RUNNING


def test_error_records_cannot_contain_output():
    with pytest.raises(ValueError):
        record(state=RunState.FAILED, output_data={"secret": 1})


def test_cas_cannot_rebind_run_identity(store):
    initial = record()
    store.create("a", initial)
    with pytest.raises(PersistenceError, match="invalid_transition"):
        store.compare_and_swap(
            "a",
            "run-1",
            0,
            initial.model_copy(
                update={"state": RunState.RUNNING, "version": 1, "artifact_revision": "other"}
            ),
        )


def service_registry():
    from pydantic import BaseModel

    class Input(BaseModel):
        value: int

    class Output(BaseModel):
        value: int

    compiled = SimpleNamespace(
        definition=SimpleNamespace(
            name="example", version="1", input_schema=Input, output_schema=Output
        ),
        definition_digest="digest",
    )
    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            status="complete", output=Output(value=kwargs["input_data"]["value"] + 1), cause=None
        )

    async def arun(**kwargs):
        return run(**kwargs)

    return SimpleNamespace(resolve=lambda name, version: compiled, run=run, arun=arun), calls


def test_service_validates_before_admission_and_executes_once(store):
    registry, calls = service_registry()
    service = RunService(registry, store, "rev")
    with pytest.raises(PersistenceError, match="invalid_input"):
        service.admit(name="example", version="1", input_data={"value": "secret"}, run_id="bad")
    assert service.get("bad") is None
    accepted = service.admit(name="example", version="1", input_data={"value": 1}, run_id="same")
    assert (
        service.admit(name="example", version="1", input_data={"value": 1}, run_id="same")
        == accepted
    )
    with pytest.raises(PersistenceError, match="run_conflict"):
        service.admit(name="example", version="1", input_data={"value": 2}, run_id="same")
    terminal = service.execute("same", expected_version=0)
    assert terminal.state == RunState.SUCCEEDED
    assert terminal.output_data == {"value": 2}
    assert terminal.version == 2
    assert calls[0]["context"].run_id == "same"
    with pytest.raises(PersistenceError, match="run_not_accepted"):
        service.execute("same", expected_version=2)
    assert len(calls) == 1


def test_service_rejects_revision_and_digest_drift_before_claim(store):
    registry, calls = service_registry()
    service = RunService(registry, store, "rev")
    accepted = service.admit(name="example", version="1", input_data={"value": 1})
    with pytest.raises(PersistenceError, match="artifact_revision_mismatch"):
        RunService(registry, store, "different").execute(accepted.run_id, expected_version=0)
    registry.resolve("example", "1").definition_digest = "new-digest"
    with pytest.raises(PersistenceError, match="definition_digest_mismatch"):
        service.execute(accepted.run_id, expected_version=0)
    assert not calls
    assert service.get(accepted.run_id).state == RunState.ACCEPTED


def test_cancel_is_durable_before_execution(store):
    registry, calls = service_registry()
    service = RunService(registry, store, "rev")
    accepted = service.admit(name="example", version="1", input_data={"value": 1})
    terminal = service.cancel(accepted.run_id, expected_version=0)
    assert terminal.state == RunState.CANCELLED
    with pytest.raises(PersistenceError, match="run_not_accepted"):
        service.execute(accepted.run_id, expected_version=1)
    assert not calls


@pytest.mark.asyncio
async def test_async_service_executes_and_persists(store):
    registry, calls = service_registry()
    service = RunService(registry, store, "rev")
    admitted = service.admit(name="example", version="1", input_data={"value": 1})
    assert (await service.aexecute(admitted.run_id, expected_version=0)).state == RunState.SUCCEEDED
    assert len(calls) == 1


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf")])
def test_invalid_timeout_does_not_claim_run(store, timeout):
    registry, calls = service_registry()
    service = RunService(registry, store, "rev")
    admitted = service.admit(name="example", version="1", input_data={"value": 1})
    with pytest.raises(PersistenceError, match="invalid_timeout"):
        service.execute(admitted.run_id, expected_version=0, timeout_seconds=timeout)
    assert service.get(admitted.run_id).state == RunState.ACCEPTED
    assert not calls


def test_registry_failure_persists_safe_error_without_output(store):
    registry, _ = service_registry()

    def explode(**kwargs):
        kwargs["input_data"]["value"] = 99
        raise RuntimeError("sensitive upstream exception")

    registry.run = explode
    service = RunService(registry, store, "rev")
    admitted = service.admit(name="example", version="1", input_data={"value": 1})
    terminal = service.execute(admitted.run_id, expected_version=0)
    assert terminal.state == RunState.FAILED
    assert terminal.output_data is None
    assert terminal.input_data == {"value": 1}
    assert terminal.cause == {"code": "execution_failed"}


def test_running_run_is_never_automatically_retried(store):
    registry, calls = service_registry()
    service = RunService(registry, store, "rev")
    admitted = service.admit(name="example", version="1", input_data={"value": 1})
    store.compare_and_swap(
        "default",
        admitted.run_id,
        0,
        admitted.model_copy(update={"state": RunState.RUNNING, "version": 1}),
    )
    with pytest.raises(PersistenceError, match="run_not_accepted"):
        service.execute(admitted.run_id, expected_version=1)
    assert not calls


def test_sqlite_initialization_errors_are_safe(tmp_path):
    with pytest.raises(PersistenceError, match=r"^store_unavailable$"):
        SQLiteRunStore(tmp_path / "secret-missing-directory" / "runs.db")


def test_real_graph_non_json_output_is_saved_as_failed(store):
    from pydantic import BaseModel

    import bm_workflow as w

    class Input(BaseModel):
        value: int

    class Output(BaseModel):
        result: float

    compiled = w.compile_workflow(
        w.workflow(name="non-json", version="1", input=Input, output=Output).flow(
            w.step("compute", lambda state: {"result": float("nan")})
        )
    )
    service = RunService(w.WorkflowRegistry([compiled]), store, "rev")
    admitted = service.admit(name="non-json", version="1", input_data={"value": 1})
    terminal = service.execute(admitted.run_id, expected_version=0)
    assert terminal.state == RunState.FAILED
    assert terminal.cause == {"code": "invalid_output"}
    assert terminal.output_data is None
    assert service.get(admitted.run_id) == terminal


@pytest.mark.parametrize("async_execution", [False, True])
def test_real_graph_strict_dates_survive_json_storage(store, async_execution):
    import asyncio

    from pydantic import BaseModel, ConfigDict, Field

    import bm_workflow as w

    class Input(BaseModel):
        model_config = ConfigDict(strict=True)
        instant: datetime = Field(validation_alias="at")
        day: date

    class Output(BaseModel):
        result: str

    compiled = w.compile_workflow(
        w.workflow(name="dates", version="1", input=Input, output=Output).flow(
            w.step(
                "compute",
                lambda state: {
                    "result": f"{state['instant'].isoformat()} {state['day'].isoformat()}"
                },
            )
        )
    )
    service = RunService(w.WorkflowRegistry([compiled]), store, "rev")
    instant = datetime(2026, 11, 2, tzinfo=UTC)
    admitted = service.admit(
        name="dates", version="1", input_data={"at": instant, "day": date(2026, 11, 2)}
    )
    assert admitted.input_data["instant"] == "2026-11-02T00:00:00Z"
    if async_execution:
        terminal = asyncio.run(service.aexecute(admitted.run_id, expected_version=0))
    else:
        terminal = service.execute(admitted.run_id, expected_version=0)
    assert terminal.state == RunState.SUCCEEDED
    assert terminal.output_data == {"result": "2026-11-02T00:00:00+00:00 2026-11-02"}


@pytest.mark.asyncio
async def test_sync_execution_inside_event_loop_leaves_run_accepted(store):
    registry, calls = service_registry()
    service = RunService(registry, store, "rev")
    admitted = service.admit(name="example", version="1", input_data={"value": 1})
    with pytest.raises(RuntimeError, match="async API"):
        service.execute(admitted.run_id, expected_version=0)
    assert service.get(admitted.run_id).state == RunState.ACCEPTED
    assert not calls
