"""Fixed execution observations shared by production and the evaluator."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from langchain_core.callbacks import BaseCallbackHandler

from fin_graph.safety.secrets import redact_value

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


_ANALYSIS_AGENT_NAMES = {
    "plan": "planner",
    "extract": "extractor",
    "judge": "judge",
    "synthesize": "synthesizer",
    "decide": "portfolio_manager",
}


@dataclass(frozen=True)
class _Branch:
    """The parallel service method: its name, the agent prefix and where its window argument is."""

    method: str
    prefix: str
    window_index: int


_ANALYSIS_BRANCH = _Branch("analyze", "analyst", 3)


class _ObservedServices:
    def __init__(
        self,
        delegate: Any,
        trace: ExecutionTrace | None,
        names: Mapping[str, str],
        branch: _Branch,
    ) -> None:
        self.delegate, self.trace, self.names, self.branch = delegate, trace, names, branch

    def __getattr__(self, method: str) -> Any:
        # Preserve optional methods (e.g. synthesize_with_context) and hasattr behavior.
        target = getattr(self.delegate, method)
        is_branch = method == self.branch.method
        if not is_branch and method not in self.names:
            return target

        async def observed(*args: Any, **kwargs: Any) -> Any:
            name = (
                f"{self.branch.prefix}_{args[0].domain.value}" if is_branch else self.names[method]
            )
            details = (
                {"window": args[self.branch.window_index].model_dump(mode="json")}
                if is_branch
                else {}
            )
            trace = self.trace or active_trace.get()
            if trace is None:
                raise RuntimeError("graph service called outside the harness scope")
            return await trace.agent(name, lambda: target(*args, **kwargs), **details)

        return observed


def observe_analysis_services[ServicesT](
    services: ServicesT, trace: ExecutionTrace | None = None
) -> ServicesT:
    """The analysis services with every model step recorded as an agent in the trace."""
    return cast(
        ServicesT, _ObservedServices(services, trace, _ANALYSIS_AGENT_NAMES, _ANALYSIS_BRANCH)
    )
