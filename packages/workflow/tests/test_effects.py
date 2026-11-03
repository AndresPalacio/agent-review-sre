import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from bm_workflow.effects import (
    EffectCoordinator,
    EffectError,
    EffectState,
    InMemoryEffectStore,
    SQLiteEffectStore,
)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    value = (
        InMemoryEffectStore()
        if request.param == "memory"
        else SQLiteEffectStore(tmp_path / "effects.db")
    )
    yield value
    if hasattr(value, "close"):
        value.close()


def test_confirmed_effect_replays_detached_result_and_rejects_changed_payload(store):
    coordinator = EffectCoordinator(store, namespace="tenant-a")
    calls = []

    def send(payload):
        calls.append(payload)
        return {"ids": [1]}

    result = coordinator.execute("send-1", send, {"amount": 10})
    result["ids"].append(2)
    assert coordinator.execute("send-1", send, {"amount": 10}) == {"ids": [1]}
    assert len(calls) == 1
    with pytest.raises(EffectError, match="effect_payload_conflict"):
        coordinator.execute("send-1", send, {"amount": 11})


def test_effect_failure_is_unknown_and_never_retries(store):
    coordinator = EffectCoordinator(store)
    calls = []

    def uncertain(payload):
        calls.append(payload)
        raise RuntimeError("secret upstream details")

    with pytest.raises(EffectError) as caught:
        coordinator.execute("uncertain", uncertain, {})
    assert str(caught.value) == "effect_outcome_unknown"
    assert "secret" not in repr(caught.value)
    assert store.get("default", "uncertain").state == EffectState.UNKNOWN
    with pytest.raises(EffectError, match="effect_outcome_unknown"):
        coordinator.execute("uncertain", uncertain, {})
    assert len(calls) == 1


def test_concurrent_pending_effect_is_not_sent_twice(store):
    entered, release = Event(), Event()
    coordinator = EffectCoordinator(store)

    def blocking(payload):
        entered.set()
        assert release.wait(5)
        return {"ok": True}

    with ThreadPoolExecutor() as pool:
        future = pool.submit(coordinator.execute, "key", blocking, {})
        assert entered.wait(5)
        try:
            with pytest.raises(EffectError, match="effect_pending"):
                coordinator.execute("key", blocking, {})
        finally:
            release.set()
        assert future.result() == {"ok": True}


def test_effect_namespaces_are_independent(store):
    assert EffectCoordinator(store, "a").execute("key", lambda p: 1, {}) == 1
    assert EffectCoordinator(store, "b").execute("key", lambda p: 2, {}) == 2


def test_sqlite_effects_survive_reopen_and_independent_connections(tmp_path):
    path = tmp_path / "effects.db"
    with SQLiteEffectStore(path) as first, SQLiteEffectStore(path) as second:
        assert EffectCoordinator(first).execute("key", lambda p: "sent", {}) == "sent"
        assert (
            EffectCoordinator(second).execute("key", lambda p: pytest.fail("duplicate"), {})
            == "sent"
        )
    with SQLiteEffectStore(path) as reopened:
        assert (
            EffectCoordinator(reopened).execute("key", lambda p: pytest.fail("duplicate"), {})
            == "sent"
        )


def test_sqlite_effect_initialization_errors_are_safe(tmp_path):
    with pytest.raises(EffectError, match=r"^effect_store_unavailable$"):
        SQLiteEffectStore(tmp_path / "secret-missing-directory" / "effects.db")


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
def test_effect_interrupts_mark_unknown_and_propagate(store, error_type):
    coordinator = EffectCoordinator(store)
    error = error_type()

    def interrupted(payload):
        raise error

    with pytest.raises(error_type) as caught:
        coordinator.execute("interrupted", interrupted, {})
    assert caught.value is error
    assert store.get("default", "interrupted").state == EffectState.UNKNOWN
    with pytest.raises(EffectError, match="effect_outcome_unknown"):
        coordinator.execute("interrupted", lambda payload: pytest.fail("replayed"), {})
