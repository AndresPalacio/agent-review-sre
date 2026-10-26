"""Per-node scopes retain native graph streaming, checkpoints and cancellation."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.runnables.config import merge_configs
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from langgraph.types import Overwrite

from fin_graph.harness.profile import RuntimeProfile
from fin_graph.harness.trace import ExecutionTrace, active_trace
from fin_graph.integrations.context import RunContext

#: Nodes that run as parallel ``Send`` branches; they must not write the shared profile channel.
PARALLEL_NODES = frozenset({"specialist", "analyst"})


class Observation:
    def __init__(self, profile: RuntimeProfile, trace: ExecutionTrace | None = None) -> None:
        self.profile, self.trace = profile, trace

    def wrap(self, name: str, node: Callable[..., Any]) -> Callable[..., Any]:
        parameters = inspect.signature(node).parameters

        async def observed(
            state: dict[str, Any], config: RunnableConfig, runtime: Runtime[RunContext]
        ) -> dict[str, Any]:
            # A resumed checkpoint must use the same installed code and budgets.
            if (
                name != "intake"
                and state.get("harness")
                and state["harness"] != self.profile.metadata()
            ):
                raise ValueError(_profile_mismatch(state["harness"], self.profile.metadata()))
            writer = get_stream_writer()

            def emit(event: dict[str, Any]) -> None:
                event["node"] = name
                if self.trace is not None:
                    self.trace.emit(
                        event["kind"],
                        event["name"],
                        event["call_id"],
                        **{
                            key: value
                            for key, value in event.items()
                            if key not in {"kind", "name", "call_id", "seq", "elapsed"}
                        },
                    )
                writer(
                    {
                        "type": "harness_trace",
                        "run_id": state.get("run_id", ""),
                        "thread_id": state.get("thread_id", ""),
                        "ts": datetime.now(UTC).isoformat(),
                        "data": event,
                    }
                )

            trace = ExecutionTrace(emit)
            token = active_trace.set(trace)

            async def call(value: dict[str, Any]) -> dict[str, Any]:
                kwargs: dict[str, Any] = {}
                if "runtime" in parameters:
                    kwargs["runtime"] = runtime
                if "config" in parameters:
                    kwargs["config"] = config
                result = node(value, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return dict(result)

            try:
                result: dict[str, Any] = await RunnableLambda(call).ainvoke(
                    state, config=merge_configs(config, {"callbacks": [trace]})
                )
            finally:
                active_trace.reset(token)
            if name == "intake":
                result["harness"] = self.profile.metadata()
                result["harness_trace"] = Overwrite(trace.events)
            else:
                result["harness_trace"] = trace.events
                # Pre-migration checkpoints had no profile. Stamp the first serial
                # node after resume; parallel specialists must not write one channel.
                if not state.get("harness") and name not in PARALLEL_NODES:
                    result["harness"] = self.profile.metadata()
            return result

        return observed


def _profile_mismatch(stored: dict[str, Any], active: dict[str, Any]) -> str:
    fields = sorted(
        key for key in stored.keys() | active.keys() if stored.get(key) != active.get(key)
    )
    environment = "FIN_ANALYSIS_*" if active.get("mode") == "analysis" else "FIN_HARNESS_*"
    run_kind = "analysis" if active.get("mode") == "analysis" else "investigation"
    return (
        "checkpoint harness profile differs from the active runtime: "
        f"variant {str(stored.get('variant'))[:19]} -> {str(active.get('variant'))[:19]}; "
        f"fields: {', '.join(fields)}. Resume with the code and {environment} limits "
        f"that started this {run_kind}."
    )
