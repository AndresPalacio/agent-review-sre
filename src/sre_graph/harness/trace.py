"""Fixed execution observations shared by production and the evaluator."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from langchain_core.callbacks import BaseCallbackHandler

from sre_graph.graphs.investigation.services import InvestigationServices
from sre_graph.safety.secrets import redact_value

active_trace: ContextVar[ExecutionTrace | None] = ContextVar("harness_trace", default=None)
_CLOCK_ID = uuid4().hex


class ExecutionTrace(BaseCallbackHandler):
    # Inline callbacks inherit the currently executing specialist's async context.
    run_inline = True
    raise_error = True

    def __init__(self, sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.events: list[dict[str, Any]] = []
        self._scope: ContextVar[str | None] = ContextVar("trace_scope", default=None)
        self._started = time.perf_counter()
        self._tools: dict[str, tuple[str, str | None]] = {}
        self._sink = sink

    def emit(self, kind: str, name: str, call_id: str, **extra: Any) -> None:
        self.events.append(
            {
                "seq": len(self.events),
                "elapsed": time.perf_counter() - self._started,
                "observed_at": datetime.now(UTC).isoformat(),
                # UTC may have coarse resolution or jump; this clock orders all
                # nodes in one process, including concurrent branches and runs.
                "clock_id": _CLOCK_ID,
                "monotonic_ns": time.perf_counter_ns(),
                "kind": kind,
                "name": name,
                "call_id": call_id,
                **redact_value(extra),
            }
        )
        if self._sink is not None:
            self._sink(self.events[-1])

    async def agent(self, name: str, action: Callable[[], Awaitable[Any]], **details: Any) -> Any:
        call_id = str(uuid4())
        self.emit("agent_start", name, call_id, parent=self._scope.get(), **details)
        token = self._scope.set(call_id)
        try:
            result = await action()
        except BaseException as exc:
            self.emit("agent_end", name, call_id, status="error", error=type(exc).__name__)
            raise
        else:
            self.emit("agent_end", name, call_id, status="ok")
            return result
        finally:
            self._scope.reset(token)

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        name, parent = str(serialized.get("name", "unknown")), self._scope.get()
        self._tools[str(run_id)] = name, parent
        self.emit("tool_start", name, str(run_id), parent=parent, args=inputs or {})

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        name, parent = self._tools.pop(str(run_id), ("unknown", None))
        self.emit(
            "tool_end",
            name,
            str(run_id),
            parent=parent,
            status="error" if getattr(output, "status", None) == "error" else "ok",
        )

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        name, parent = self._tools.pop(str(run_id), ("unknown", None))
        self.emit(
            "tool_end", name, str(run_id), parent=parent, status="error", error=type(error).__name__
        )


_AGENT_NAMES = {
    "plan": "planner",
    "extract": "extractor",
    "judge": "judge",
    "synthesize": "synthesizer",
    "synthesize_with_context": "synthesizer",
}


class _ObservedServices:
    def __init__(self, delegate: InvestigationServices, trace: ExecutionTrace | None) -> None:
        self.delegate, self.trace = delegate, trace

    def __getattr__(self, method: str) -> Any:
        # Preserve optional methods (e.g. synthesize_with_context) and hasattr behavior.
        target = getattr(self.delegate, method)
        if method != "investigate" and method not in _AGENT_NAMES:
            return target

        async def observed(*args: Any, **kwargs: Any) -> Any:
            name = (
                f"specialist_{args[0].domain.value}"
                if method == "investigate"
                else _AGENT_NAMES[method]
            )
            details = {"window": args[2].model_dump(mode="json")} if method == "investigate" else {}
            trace = self.trace or active_trace.get()
            if trace is None:
                raise RuntimeError("investigation service called outside the harness scope")
            return await trace.agent(name, lambda: target(*args, **kwargs), **details)

        return observed


def observe_services(
    services: InvestigationServices, trace: ExecutionTrace | None = None
) -> InvestigationServices:
    return cast(InvestigationServices, _ObservedServices(services, trace))
