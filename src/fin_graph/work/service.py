"""One local gateway process, one execution engine (LangGraph Agent Server)."""

import asyncio
import hashlib
import json
import time
import uuid
from typing import Any, Protocol

from fin_graph.safety.secrets import redact_text, redact_value
from fin_graph.work.contracts import (
    CancelWork,
    StartWork,
    UpdateWork,
    WorkConflict,
    WorkUnavailable,
    WorkView,
)
from fin_graph.work.policy import DecisionPolicy, Route, RulesPolicy
from fin_graph.work.store import WorkStore


class WorkEngine(Protocol):
    async def ensure_thread(self, work_id: str, metadata: dict[str, Any]) -> None: ...

    async def submit(
        self,
        work_id: str,
        text: str,
        command_id: str,
        context: dict[str, Any],
        *,
        enqueue: bool = False,
    ) -> str: ...

    async def find_run(self, work_id: str, command_id: str) -> str | None: ...

    async def snapshot(self, work_id: str) -> dict[str, Any]: ...

    async def cancel_tree(self, work_id: str) -> bool: ...


# Instances sharing a ledger in one process also share serialization. This is
# deliberately not a distributed lock; HTTP and MCP are mounted in one gateway.
_LOCKS: dict[str, asyncio.Lock] = {}
_ACTIVE_CHILD = {"running", "pending", "busy", "queued"}


class WorkService:
    def __init__(
        self, store: WorkStore, engine: WorkEngine, policy: DecisionPolicy | None = None
    ) -> None:
        self.store = store
        self.engine = engine
        self.policy = policy or RulesPolicy()
        self._lock = _LOCKS.setdefault(str(store.path), asyncio.Lock())

    def _owned(self, work_id: str, actor: str) -> dict[str, Any]:
        try:
            work = self.store.get(work_id)
        except KeyError:
            raise PermissionError("Trabajo no disponible para este actor") from None
        if work["actor"] != actor:
            raise PermissionError("Trabajo no disponible para este actor")
        return work

    @staticmethod
    def _identity(actor: str, kind: str, work_id: str, payload: Any) -> dict[str, Any]:
        serialized = json.dumps([kind, work_id, payload.model_dump()], sort_keys=True)
        return {
            "actor": actor,
            "command_id": payload.command_id,
            "fingerprint": hashlib.sha256(serialized.encode()).hexdigest(),
            "work_id": work_id,
            "state": "dispatching",
            "run_id": None,
        }

    async def _existing(self, command: dict[str, Any]) -> WorkView | None:
        old = self.store.command(command["actor"], command["command_id"])
        if old is None:
            return None
        if old["fingerprint"] != command["fingerprint"]:
            raise WorkConflict("command_id ya usado para otro contenido")
        work = self._owned(old["work_id"], command["actor"])
        if old["state"] == "dispatching" and not await self._reconcile(work, old):
            raise WorkUnavailable(
                "Despacho incierto; conserva command_id y revisa el Agent Server. "
                "No se reenvía automáticamente."
            )
        return await self._refresh(work)

    async def _reconcile(self, work: dict[str, Any], row: dict[str, Any]) -> bool:
        """Record a run the engine accepted for a dispatching command; never resubmit."""
        try:
            run_id = await self.engine.find_run(work["work_id"], row["command_id"])
        except Exception:
            raise WorkUnavailable("Agent Server no disponible; conserva el work_id") from None
        if run_id is None:
            return False
        row.update(state="dispatched", run_id=run_id)
        if work.get("latest_command_id") == row["command_id"]:
            work.update(run_id=run_id, status="running", detail=None)
        self.store.save(work, command=row)
        return True

    @staticmethod
    def _view(work: dict[str, Any]) -> WorkView:
        return WorkView.model_validate(work)

    def _context(self, work: dict[str, Any]) -> dict[str, Any]:
        return {
            **{
                key: work[key]
                for key in (
                    "actor",
                    "work_id",
                    "revision",
                    "deadline",
                    "delay_seconds",
                    "max_children",
                )
            },
            "ledger_path": str(self.store.path),
        }

    async def _dispatch(
        self,
        work: dict[str, Any],
        command: dict[str, Any],
        text: str,
        *,
        enqueue: bool,
        consolidating: bool = False,
    ) -> WorkView:
        try:
            # Thread creation is idempotent and starts no execution. A failure
            # here is safely retryable and must not become an uncertain run.
            await self.engine.ensure_thread(work["work_id"], {"actor": work["actor"]})
        except Exception:
            raise WorkUnavailable("No se pudo preparar el trabajo; reintenta el comando") from None
        work["latest_command_id"] = command["command_id"]
        self.store.save(work, command=command)
        try:
            run_id = await self.engine.submit(
                work["work_id"],
                text,
                command["command_id"],
                {**self._context(work), "consolidating": consolidating},
                enqueue=enqueue,
            )
        except Exception:
            # A timeout may occur AFTER the server accepts a run. Persisting the
            # intent before this call makes a retry reconcile instead of duplicate.
            raise WorkUnavailable("Despacho incierto; reintenta con el mismo command_id") from None
        command.update(state="dispatched", run_id=run_id)
        work.update(run_id=run_id, status="running")
        self.store.save(work, command=command)
        return self._view(work)

    async def start(self, command: StartWork, *, actor: str) -> WorkView:
        work_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sre-work:{actor}:{command.command_id}"))
        identity = self._identity(actor, "start", work_id, command)
        async with self._lock:
            if existing := await self._existing(identity):
                return existing
        # Routing may call a provider; do not serialize every other work behind it.
        text = redact_text(command.text)
        decision = await self.policy.decide(text)
        async with self._lock:
            # A concurrent retry of this command may have created the work meanwhile.
            if existing := await self._existing(identity):
                return existing
            work: dict[str, Any] = {
                "work_id": work_id,
                "actor": actor,
                "revision": 1,
                "status": "dispatching",
                "run_id": None,
                "result": None,
                "decision": decision.model_dump(mode="json"),
                "children": [],
                "decision_trace": [],
                "cancel_requested": False,
                "deadline": time.time() + command.deadline_seconds,
                "delay_seconds": command.delay_seconds,
                "max_children": 2 if decision.route == Route.INVESTIGATE else 0,
            }
            if decision.route == Route.CLARIFY:
                work.update(status="completed", result="Indica el incidente, servicio y periodo.")
                identity["state"] = "local"
                self.store.save(work, command=identity)
                return self._view(work)
            return await self._dispatch(work, identity, text, enqueue=False)

    async def get(self, work_id: str, *, actor: str) -> WorkView:
        async with self._lock:
            return await self._refresh(self._owned(work_id, actor))

    async def _refresh(self, work: dict[str, Any]) -> WorkView:
        if work["status"] == "completed":
            # A delayed notification cannot reopen or erase an observed final result.
            return self._view(work)
        if work["cancel_requested"]:
            try:
                confirmed = await self.engine.cancel_tree(work["work_id"])
            except Exception:
                confirmed = False
            work.update(status="cancelled" if confirmed else "cancel_requested", result=None)
            if confirmed:
                # The engine confirmed no active run remains anywhere in the tree.
                for child in work["children"]:
                    if child.get("status") in _ACTIVE_CHILD:
                        child["status"] = "cancelled"
            self.store.save(work)
            return self._view(work)
        if work["decision"]["route"] == Route.CLARIFY:
            return self._view(work)
        latest = work.get("latest_command_id")
        row = self.store.command(work["actor"], latest) if latest else None
        if (
            row is not None
            and row["state"] == "dispatching"
            and not await self._reconcile(work, row)
        ):
            # The engine lists no run for the latest command: nothing executes it.
            work.update(
                status="dispatch_uncertain",
                result=None,
                detail="El Agent Server no registra el run; reintenta el comando o cancela",
            )
            self.store.save(work)
            return self._view(work)
        try:
            snapshot = await self.engine.snapshot(work["work_id"])
        except Exception:
            raise WorkUnavailable("Agent Server no disponible; conserva el work_id") from None
        values = snapshot.get("values") or {}
        children = snapshot.get("children", [])
        work.update(
            children=redact_value(children),
            decision_trace=redact_value(values.get("decision_trace", [])),
            model_mode=values.get("model_mode", "unknown"),
            result=None,
        )
        status = snapshot.get("status")
        if status == "interrupted" or any(
            child.get("status") in {"interrupted", "waiting_for_input"} for child in children
        ):
            work["status"] = "waiting_for_input"
        elif status == "error" or any(
            child.get("status") in {"error", "recovery_required"} for child in children
        ):
            work["status"] = "failed"
        elif status == "busy" or any(child.get("status") in _ACTIVE_CHILD for child in children):
            work["status"] = "running"
        elif any(child.get("status") == "awaiting_parent" for child in children):
            work["status"] = "running"
            messages = values.get("messages") or [{}]
            return await self._consolidate(
                work,
                {
                    "last_message": messages[-1],
                    "pending_notifications": sorted(
                        [
                            (child.get("task_id"), child.get("run_id"))
                            for child in children
                            if child.get("status") == "awaiting_parent"
                        ]
                    ),
                },
            )
        elif (
            values.get("work_revision") == work["revision"]
            and values.get("result_revision") == work["revision"]
        ):
            messages = values.get("messages", [])
            last = messages[-1] if messages else {}
            if last.get("type", last.get("role")) in {"ai", "assistant"} and not last.get(
                "tool_calls"
            ):
                content = last.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(
                        item.get("text", "") for item in content if isinstance(item, dict)
                    )
                work.update(status="completed", result=redact_text(str(content)))
            else:
                work["status"] = "running"
        else:
            work["status"] = "running"
            if (
                status == "idle"
                and children
                and all(child.get("status") in {"success", "cancelled"} for child in children)
                and values.get("work_revision") == work["revision"]
                and values.get("result_revision") is not None
                and values["result_revision"] != work["revision"]
            ):
                messages = values.get("messages") or [{}]
                return await self._consolidate(work, messages[-1])
        self.store.save(work)
        return self._view(work)

    async def _consolidate(self, work: dict[str, Any], stale_message: dict[str, Any]) -> WorkView:
        # An untouched child may wake the parent with an older context after an
        # update. Ask the same supervisor for a current-revision answer once all
        # children are terminal. This is a persisted command, not a second worker.
        marker = hashlib.sha256(json.dumps(stale_message, sort_keys=True).encode()).hexdigest()[:16]
        command = UpdateWork(
            command_id=f"consolidate:{work['work_id']}:{work['revision']}:{marker}",
            expected_revision=work["revision"],
            message="Consolida los resultados actuales del trabajo sin iniciar ni actualizar hijos",
        )
        identity = self._identity(work["actor"], "consolidate", work["work_id"], command)
        old = self.store.command(work["actor"], command.command_id)
        if old is not None:
            if old["fingerprint"] != identity["fingerprint"]:
                raise WorkConflict("Conflicto con un identificador interno de consolidación")
            if old["state"] == "dispatching" and not await self._reconcile(work, old):
                raise WorkUnavailable("Consolidación con despacho incierto; no se duplica")
            self.store.save(work, command=old)
            return self._view(work)
        counts = work.setdefault("consolidations", {})
        revision = str(work["revision"])
        if counts.get(revision, 0) >= 3:
            work.update(status="failed", detail="Se agotó el límite de consolidaciones de revisión")
            self.store.save(work)
            return self._view(work)
        counts[revision] = counts.get(revision, 0) + 1
        return await self._dispatch(
            work, identity, command.message, enqueue=True, consolidating=True
        )

    async def update(self, work_id: str, command: UpdateWork, *, actor: str) -> WorkView:
        async with self._lock:
            work = self._owned(work_id, actor)
            identity = self._identity(actor, "update", work_id, command)
            if existing := await self._existing(identity):
                return existing
            self._check_revision(work, command.expected_revision)
            current = await self._refresh(work)
            if current.status == "waiting_for_input":
                raise WorkConflict("El trabajo espera entrada/aprobación; no se sobrescribe")
            if current.status == "dispatch_uncertain":
                raise WorkConflict("Despacho incierto; reintenta el comando original o cancela")
            if current.status in {"completed", "failed", "cancelled", "cancel_requested"}:
                raise WorkConflict("El trabajo ya es terminal; inicia otro trabajo")
            if time.time() >= work["deadline"]:
                raise WorkConflict("El plazo del trabajo ha expirado")
            work.update(revision=work["revision"] + 1, status="dispatching", result=None)
            return await self._dispatch(work, identity, redact_text(command.message), enqueue=True)

    @staticmethod
    def _check_revision(work: dict[str, Any], expected: int) -> None:
        if work["cancel_requested"]:
            raise WorkConflict("El trabajo tiene una cancelación solicitada")
        if work["revision"] != expected:
            raise WorkConflict(f"Revisión obsoleta; revisión actual: {work['revision']}")

    async def cancel(self, work_id: str, command: CancelWork, *, actor: str) -> WorkView:
        async with self._lock:
            work = self._owned(work_id, actor)
            identity = self._identity(actor, "cancel", work_id, command)
            if existing := await self._existing(identity):
                return existing
            if work["status"] in {"completed", "cancelled"}:
                raise WorkConflict("El trabajo ya es terminal")
            self._check_revision(work, command.expected_revision)
            work.update(
                revision=work["revision"] + 1,
                cancel_requested=True,
                status="cancel_requested",
                result=None,
            )
            identity["state"] = "local"
            self.store.save(work, command=identity)
            return await self._refresh(work)
