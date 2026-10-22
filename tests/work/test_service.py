from __future__ import annotations

import copy
import uuid

import pytest

from sre_graph.work.contracts import (
    CancelWork,
    StartWork,
    UpdateWork,
    WorkConflict,
    WorkUnavailable,
)
from sre_graph.work.service import WorkService
from sre_graph.work.store import WorkStore


class MemoryEngine:
    def __init__(self):
        self.submissions = []
        self.states = {}
        self.lose_response = False
        self.cancel_finishes = True

    async def ensure_thread(self, work_id, metadata):
        self.states.setdefault(work_id, {"status": "idle", "values": {}, "children": []})

    async def submit(self, work_id, text, command_id, context, *, enqueue=False):
        run_id = f"run-{len(self.submissions) + 1}"
        self.submissions.append((work_id, command_id, run_id, text, context, enqueue))
        self.states[work_id] = {
            "status": "busy",
            "values": {"work_revision": context["revision"]},
            "children": [],
        }
        if self.lose_response:
            self.lose_response = False
            raise TimeoutError("password=must-not-leak")
        return run_id

    async def find_run(self, work_id, command_id):
        return next((r[2] for r in self.submissions if r[:2] == (work_id, command_id)), None)

    async def snapshot(self, work_id):
        return copy.deepcopy(self.states[work_id])

    async def cancel_tree(self, work_id):
        if self.cancel_finishes:
            self.states[work_id]["status"] = "idle"
        return self.cancel_finishes


def service(tmp_path, engine=None):
    engine = engine or MemoryEngine()
    return WorkService(WorkStore(tmp_path / "work.sqlite"), engine), engine


async def test_start_is_idempotent_across_gateway_restart_and_payload_conflicts(tmp_path):
    gateway, engine = service(tmp_path)
    command = StartWork(command_id="incident-1", text="Investiga latencia de pagos")
    first = await gateway.start(command, actor="ana")
    restarted, _ = service(tmp_path, engine)
    again = await restarted.start(command, actor="ana")
    assert first.work_id == again.work_id
    assert len(engine.submissions) == 1
    with pytest.raises(WorkConflict):
        await restarted.start(command.model_copy(update={"text": "otro"}), actor="ana")


async def test_owner_and_revision_are_enforced_before_dispatch(tmp_path):
    gateway, engine = service(tmp_path)
    first = await gateway.start(StartWork(command_id="one", text="Investiga pagos"), actor="ana")
    with pytest.raises(PermissionError):
        await gateway.get(first.work_id, actor="otro")
    update = UpdateWork(command_id="u1", expected_revision=1, message="Revisa últimos 5 minutos")
    second = await gateway.update(first.work_id, update, actor="ana")
    assert second.revision == 2
    assert engine.submissions[-1][-1] is True
    with pytest.raises(WorkConflict):
        await gateway.update(
            first.work_id, update.model_copy(update={"command_id": "u2"}), actor="ana"
        )
    assert len(engine.submissions) == 2
    repeated = await gateway.update(first.work_id, update, actor="ana")
    assert repeated.revision == 2
    assert len(engine.submissions) == 2


async def test_lost_dispatch_response_reconciles_without_resubmitting(tmp_path):
    gateway, engine = service(tmp_path)
    engine.lose_response = True
    command = StartWork(command_id="lost", text="Investiga latencia de pagos")
    with pytest.raises(WorkUnavailable, match="incierto") as exc:
        await gateway.start(command, actor="ana")
    assert "must-not-leak" not in str(exc.value)
    restarted, _ = service(tmp_path, engine)
    recovered = await restarted.start(command, actor="ana")
    assert recovered.run_id == "run-1"
    assert len(engine.submissions) == 1


async def test_cancel_intent_survives_restart_and_never_accepts_late_result(tmp_path):
    gateway, engine = service(tmp_path)
    work = await gateway.start(StartWork(command_id="c", text="Investiga pagos"), actor="ana")
    engine.cancel_finishes = False
    command = CancelWork(command_id="cancel", expected_revision=1)
    cancelled = await gateway.cancel(work.work_id, command, actor="ana")
    assert cancelled.status == "cancel_requested"
    engine.states[work.work_id] = {
        "status": "idle",
        "values": {"messages": [{"type": "ai", "content": "late"}]},
        "children": [],
    }
    engine.cancel_finishes = True
    restarted, _ = service(tmp_path, engine)
    final = await restarted.get(work.work_id, actor="ana")
    assert final.status == "cancelled"
    assert final.result is None
    with pytest.raises(WorkConflict):
        await restarted.update(
            work.work_id,
            UpdateWork(command_id="after", expected_revision=2, message="continua"),
            actor="ana",
        )


async def test_completed_parent_with_running_child_is_still_running(tmp_path):
    gateway, engine = service(tmp_path)
    work = await gateway.start(StartWork(command_id="p", text="Investiga pagos"), actor="ana")
    engine.states[work.work_id] = {
        "status": "idle",
        "values": {"messages": [{"type": "ai", "content": "iniciado"}], "work_revision": 1},
        "children": [{"task_id": "child", "status": "running", "agent_name": "logs"}],
    }
    view = await gateway.get(work.work_id, actor="ana")
    assert view.status == "running"
    assert view.result is None


async def test_pending_approval_and_stale_result_are_not_overwritten(tmp_path):
    gateway, engine = service(tmp_path)
    work = await gateway.start(StartWork(command_id="w", text="Investiga pagos"), actor="ana")
    engine.states[work.work_id]["status"] = "interrupted"
    with pytest.raises(WorkConflict, match="entrada"):
        await gateway.update(
            work.work_id,
            UpdateWork(command_id="u", expected_revision=1, message="cambia"),
            actor="ana",
        )
    engine.states[work.work_id] = {
        "status": "idle",
        "values": {"work_revision": 0, "messages": [{"type": "ai", "content": "antiguo"}]},
        "children": [],
    }
    assert (await gateway.get(work.work_id, actor="ana")).result is None


async def test_cancel_completed_local_work_is_a_conflict(tmp_path):
    gateway, engine = service(tmp_path)
    work = await gateway.start(StartWork(command_id="ambiguous", text="ayuda"), actor="ana")
    with pytest.raises(WorkConflict, match="terminal"):
        await gateway.cancel(
            work.work_id, CancelWork(command_id="stop", expected_revision=1), actor="ana"
        )
    assert not engine.submissions


async def test_reconciling_old_command_preserves_latest_run(tmp_path):
    gateway, engine = service(tmp_path)
    first = await gateway.start(StartWork(command_id="s", text="Investiga pagos"), actor="ana")
    old = UpdateWork(command_id="old", expected_revision=1, message="actualiza logs")
    engine.lose_response = True
    with pytest.raises(WorkUnavailable):
        await gateway.update(first.work_id, old, actor="ana")
    latest = await gateway.update(
        first.work_id,
        UpdateWork(command_id="new", expected_revision=2, message="actualiza métricas"),
        actor="ana",
    )
    reconciled = await gateway.update(first.work_id, old, actor="ana")
    assert reconciled.revision == latest.revision == 3
    assert reconciled.run_id == latest.run_id == "run-3"


async def test_old_notification_consolidates_once_without_returning_stale_result(tmp_path):
    gateway, engine = service(tmp_path)
    work = await gateway.start(
        StartWork(command_id="revision", text="Investiga pagos"), actor="ana"
    )
    work = await gateway.update(
        work.work_id,
        UpdateWork(command_id="revision2", expected_revision=1, message="actualiza logs"),
        actor="ana",
    )
    old_snapshot = {
        "status": "idle",
        "values": {
            "work_revision": 2,
            "result_revision": 1,
            "messages": [{"type": "ai", "content": "OLD_REVISION_1_EVIDENCE"}],
        },
        "children": [{"status": "success", "agent_name": "metrics"}],
    }
    engine.states[work.work_id] = copy.deepcopy(old_snapshot)
    view = await gateway.get(work.work_id, actor="ana")
    assert view.result is None
    assert len(engine.submissions) == 3
    assert engine.submissions[-1][4]["revision"] == 2
    engine.states[work.work_id] = copy.deepcopy(old_snapshot)
    restarted, _ = service(tmp_path, engine)
    assert (await restarted.get(work.work_id, actor="ana")).result is None
    assert len(engine.submissions) == 3
    engine.states[work.work_id]["values"].update(
        result_revision=2, messages=[{"type": "ai", "content": "CURRENT_REVISION_2_EVIDENCE"}]
    )
    final = await restarted.get(work.work_id, actor="ana")
    assert final.status == "completed"
    assert final.result == "CURRENT_REVISION_2_EVIDENCE"
    engine.states[work.work_id] = copy.deepcopy(old_snapshot)
    late = await restarted.get(work.work_id, actor="ana")
    assert late.status == "completed"
    assert late.result == "CURRENT_REVISION_2_EVIDENCE"


async def test_each_late_notification_can_be_consolidated_before_completion_is_observed(tmp_path):
    gateway, engine = service(tmp_path)
    work = await gateway.start(StartWork(command_id="late", text="Investiga pagos"), actor="ana")
    work = await gateway.update(
        work.work_id,
        UpdateWork(command_id="u", expected_revision=1, message="actualiza logs"),
        actor="ana",
    )
    for message_id in ("notification-one", "notification-two", "notification-two"):
        engine.states[work.work_id] = {
            "status": "idle",
            "values": {
                "work_revision": 2,
                "result_revision": 1,
                "messages": [{"id": message_id, "type": "ai", "content": "old"}],
            },
            "children": [{"status": "success"}],
        }
        assert (await gateway.get(work.work_id, actor="ana")).result is None
    assert len(engine.submissions) == 4


def test_ledger_connections_are_closed_after_use(tmp_path):
    store = WorkStore(tmp_path / "handles.sqlite")
    store.save({"work_id": "w", "actor": "a", "revision": 1, "cancel_requested": False})
    assert store.get("w")["revision"] == 1
    # Windows forbids removal while SQLite handles remain open.
    store.path.unlink()


async def test_retry_recovers_failure_before_run_submission(tmp_path):
    class RecoverableEngine(MemoryEngine):
        attempts = 0

        async def ensure_thread(self, work_id, metadata):
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("provider secret")
            await super().ensure_thread(work_id, metadata)

    engine = RecoverableEngine()
    gateway, _ = service(tmp_path, engine)
    command = StartWork(command_id="prepare", text="Investiga pagos")
    with pytest.raises(WorkUnavailable):
        await gateway.start(command, actor="ana")
    view = await gateway.start(command, actor="ana")
    assert view.run_id == "run-1"
    assert engine.attempts == 2
    assert len(engine.submissions) == 1


async def test_lost_child_notification_wakes_parent_once(tmp_path):
    gateway, engine = service(tmp_path)
    work = await gateway.start(StartWork(command_id="notify", text="Investiga pagos"), actor="ana")
    stale = {
        "status": "idle",
        "values": {
            "work_revision": 1,
            "result_revision": 1,
            "messages": [{"type": "ai", "content": "launched"}],
        },
        "children": [{"task_id": "child", "run_id": "cr", "status": "awaiting_parent"}],
    }
    for _ in range(2):
        engine.states[work.work_id] = copy.deepcopy(stale)
        assert (await gateway.get(work.work_id, actor="ana")).result is None
    assert len(engine.submissions) == 2
    assert engine.submissions[-1][4]["consolidating"] is True


class RejectingEngine(MemoryEngine):
    """The server refuses the run before accepting it; nothing is recorded."""

    reject = True

    async def submit(self, work_id, text, command_id, context, *, enqueue=False):
        if self.reject:
            raise ConnectionError("refused before acceptance")
        return await super().submit(work_id, text, command_id, context, enqueue=enqueue)


async def test_rejected_dispatch_is_reported_as_uncertain_not_running(tmp_path):
    engine = RejectingEngine()
    gateway, _ = service(tmp_path, engine)
    command = StartWork(command_id="refused", text="Investiga pagos")
    with pytest.raises(WorkUnavailable):
        await gateway.start(command, actor="ana")
    work_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "sre-work:ana:refused"))
    view = await gateway.get(work_id, actor="ana")
    assert view.status == "dispatch_uncertain"
    assert view.run_id is None
    assert view.detail
    with pytest.raises(WorkConflict, match="incierto"):
        await gateway.update(
            work_id, UpdateWork(command_id="u", expected_revision=1, message="m"), actor="ana"
        )
    cancelled = await gateway.cancel(
        work_id, CancelWork(command_id="c", expected_revision=1), actor="ana"
    )
    assert cancelled.status == "cancelled"
    assert engine.submissions == []


async def test_uncertain_dispatch_is_reconciled_once_the_run_is_visible(tmp_path):
    engine = RejectingEngine()
    gateway, _ = service(tmp_path, engine)
    with pytest.raises(WorkUnavailable):
        await gateway.start(StartWork(command_id="late", text="Investiga pagos"), actor="ana")
    work_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "sre-work:ana:late"))
    assert (await gateway.get(work_id, actor="ana")).status == "dispatch_uncertain"
    # The original request was in fact accepted; the engine now lists it.
    engine.submissions.append((work_id, "late", "run-late", "", {}, False))
    engine.states[work_id] = {"status": "busy", "values": {}, "children": []}
    view = await gateway.get(work_id, actor="ana")
    assert view.status == "running"
    assert view.run_id == "run-late"


async def test_unreachable_engine_during_consolidation_reconcile_is_unavailable(tmp_path):
    class FlakyEngine(RejectingEngine):
        reject = False
        lookup_fails = False

        async def find_run(self, work_id, command_id):
            if self.lookup_fails:
                raise ConnectionError("agent server down")
            return await super().find_run(work_id, command_id)

    engine = FlakyEngine()
    gateway, _ = service(tmp_path, engine)
    work = await gateway.start(StartWork(command_id="cons", text="Investiga pagos"), actor="ana")
    engine.states[work.work_id] = {
        "status": "idle",
        "values": {"work_revision": 1, "messages": [{"type": "ai", "content": "x"}]},
        "children": [{"task_id": "t", "run_id": "r", "status": "awaiting_parent"}],
    }
    engine.reject = True
    with pytest.raises(WorkUnavailable):
        await gateway.get(work.work_id, actor="ana")
    engine.lookup_fails = True
    with pytest.raises(WorkUnavailable):
        await gateway.get(work.work_id, actor="ana")


async def test_slow_routing_does_not_block_other_work_and_stays_idempotent(tmp_path):
    import asyncio

    from sre_graph.work.policy import RulesPolicy

    release = asyncio.Event()
    entered = asyncio.Event()

    class SlowPolicy(RulesPolicy):
        async def decide(self, text, **kwargs):
            if "lento" in text:
                entered.set()
                await release.wait()
            return await super().decide(text, **kwargs)

    engine = MemoryEngine()
    gateway = WorkService(WorkStore(tmp_path / "work.sqlite"), engine, SlowPolicy())
    other = await gateway.start(StartWork(command_id="fast", text="Investiga pagos"), actor="ana")
    slow = StartWork(command_id="slow", text="Investiga incidente lento")
    first = asyncio.create_task(gateway.start(slow, actor="ana"))
    second = asyncio.create_task(gateway.start(slow, actor="ana"))
    await asyncio.wait_for(entered.wait(), 1)
    view = await asyncio.wait_for(gateway.get(other.work_id, actor="ana"), 1)
    assert view.work_id == other.work_id
    release.set()
    a, b = await asyncio.gather(first, second)
    assert a.work_id == b.work_id
    assert [s[1] for s in engine.submissions].count("slow") == 1


async def test_confirmed_cancel_does_not_report_children_as_running(tmp_path):
    gateway, engine = service(tmp_path)
    work = await gateway.start(StartWork(command_id="tree", text="Investiga pagos"), actor="ana")
    engine.states[work.work_id]["children"] = [
        {"task_id": "m", "agent_name": "metrics", "status": "running", "engine_status": "busy"},
        {"task_id": "l", "agent_name": "logs", "status": "success", "engine_status": "idle"},
    ]
    assert (await gateway.get(work.work_id, actor="ana")).children[0]["status"] == "running"
    view = await gateway.cancel(
        work.work_id, CancelWork(command_id="c", expected_revision=1), actor="ana"
    )
    assert view.status == "cancelled"
    assert [c["status"] for c in view.children] == ["cancelled", "success"]
    engine.cancel_finishes = False
    pending = await gateway.start(StartWork(command_id="slow", text="Investiga pagos"), actor="ana")
    engine.states[pending.work_id]["children"] = [{"task_id": "x", "status": "running"}]
    await gateway.get(pending.work_id, actor="ana")
    view = await gateway.cancel(
        pending.work_id, CancelWork(command_id="c2", expected_revision=1), actor="ana"
    )
    assert view.status == "cancel_requested"
    assert view.children[0]["status"] == "running"
