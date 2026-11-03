"""Host-owned solver contracts; importing these never loads optional agents."""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_serializer, field_validator

from bm_workflow.definition import validate_model
from bm_workflow.errors import ExecutionError
from bm_workflow.runtime import RunContext


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


class ProblemSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)
    id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    inputs: Mapping[str, JsonValue] = Field(default_factory=dict)
    constraints: tuple[str, ...] = ()
    acceptance_criteria: Mapping[str, str]

    @field_validator("inputs")
    @classmethod
    def freeze_inputs(cls, value: dict[str, JsonValue]) -> Any:
        return _freeze(value)

    @field_serializer("inputs")
    def serialize_inputs(self, value: Any) -> Any:
        return _thaw(value)

    @field_validator("acceptance_criteria")
    @classmethod
    def immutable_criteria(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        if not value or any(not k.strip() or not v.strip() for k, v in value.items()):
            raise ValueError("acceptance criteria must contain nonempty IDs and descriptions")
        return MappingProxyType(dict(value))

    @field_serializer("acceptance_criteria")
    def serialize_criteria(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)


class VerificationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    criterion_id: str
    passed: bool
    detail: str = ""
    evidence_refs: tuple[str, ...] = ()


class VerificationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    verdict: Literal["verified", "failed", "blocked", "inconclusive"]
    checks: tuple[VerificationCheck, ...] = ()
    snapshot_digest: str | None = None
    missing_requirements: tuple[str, ...] = ()
    reason: str = ""

    def validated_for(self, problem: ProblemSpec) -> VerificationReport:
        if self.verdict != "verified":
            return self
        ids = [check.criterion_id for check in self.checks]
        if (
            self.missing_requirements
            or len(ids) != len(set(ids))
            or set(ids) != set(problem.acceptance_criteria)
            or not all(check.passed for check in self.checks)
        ):
            return self.model_copy(
                update={
                    "verdict": "inconclusive",
                    "reason": "Incomplete or contradictory acceptance checks",
                }
            )
        return self


class SolverLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    max_model_calls: int = Field(default=24, ge=0)
    max_tool_calls: int = Field(default=48, ge=0)
    max_summary_calls: int = Field(default=4, ge=0)
    max_concurrent_specialists: int = Field(default=2, ge=1)
    timeout_seconds: float = Field(default=120, gt=0, allow_inf_nan=False)
    max_repairs: int = Field(default=2, ge=0, le=2)
    verifier_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)


class SolverUsage(BaseModel):
    model_config = ConfigDict(frozen=True)
    model_calls: int = 0
    tool_calls: int = 0
    summary_calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_seconds: float = 0


class SolverResult[SolutionT](BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    candidate: SolutionT | None = None
    verification: VerificationReport
    attempts: int = 0
    usage: SolverUsage = Field(default_factory=SolverUsage)

    @property
    def verified(self) -> bool:
        return self.candidate is not None and self.verification.verdict == "verified"


class SolverStopped(Exception):
    """Safe reason for stopping the native graph without claiming resolution."""


@dataclass
class SolverContext:
    """One invocation only; host clients and workflow controls retain identity.

    Native tools receive this through ToolRuntime.context. Use host_context for
    application dependencies. A fresh context is mandatory for each graph run.
    """

    problem: ProblemSpec
    host_context: Any = None
    workflow_context: RunContext | None = None
    run_id: str = field(default_factory=lambda: str(uuid4()), init=False)
    _lock: Any = field(default_factory=threading.Lock, init=False, repr=False)
    _started: float | None = field(default=None, init=False)
    _limits: SolverLimits = field(default_factory=SolverLimits, init=False)
    _counts: dict[str, int] = field(default_factory=dict, init=False)
    _specialists: int = field(default=0, init=False)
    _attempts: int = field(default=0, init=False)
    _stop_reason: str | None = field(default=None, init=False)
    _observer: Callable[[Mapping[str, Any]], None] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        # Frozen Pydantic instances may still originate from unchecked construct/copy APIs.
        self.problem = cast(ProblemSpec, validate_model(ProblemSpec, self.problem))

    @property
    def cancellation(self) -> threading.Event | None:
        return self.workflow_context.cancellation if self.workflow_context else None

    @property
    def deadline(self) -> float | None:
        own = self._started + self._limits.timeout_seconds if self._started is not None else None
        host = self.workflow_context.deadline if self.workflow_context else None
        return min(own, host) if own is not None and host is not None else own or host

    def start(self, limits: SolverLimits, observer: Any) -> None:
        with self._lock:
            if self._started is not None:
                raise ValueError("SolverContext cannot be reused; create one per invocation")
            validated_limits = cast(SolverLimits, validate_model(SolverLimits, limits))
            self._started = time.monotonic()
            self._limits = validated_limits
            self._observer = observer

    def check(self) -> None:
        if self.cancellation is not None and self.cancellation.is_set():
            raise ExecutionError("cancelled")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self._stop_reason = "deadline_exceeded"
        if self._stop_reason:
            raise SolverStopped(self._stop_reason)

    def reserve(self, kind: str) -> None:
        with self._lock:
            self.check()
            limit = getattr(self._limits, f"max_{kind}")
            count = self._counts.get(kind, 0)
            if count >= limit:
                self._stop_reason = f"{kind}_exhausted"
                raise SolverStopped(self._stop_reason)
            self._counts[kind] = count + 1
        self.emit(kind, count=count + 1)

    def emit(self, event: str, **metadata: Any) -> None:
        if self._observer:
            with suppress(Exception):
                self._observer(
                    MappingProxyType(
                        {
                            "event": event,
                            "run_id": self.workflow_context.run_id
                            if self.workflow_context
                            else self.run_id,
                            "problem_id": self.problem.id,
                            **metadata,
                        }
                    )
                )

    def record_tokens(self, usage: Any) -> None:
        if usage:
            with self._lock:
                for key in ("input_tokens", "output_tokens"):
                    self._counts[key] = self._counts.get(key, 0) + usage.get(key, 0)

    def usage(self) -> SolverUsage:
        return SolverUsage(
            **self._counts, duration_seconds=time.monotonic() - (self._started or time.monotonic())
        )

    async def controlled(
        self, operation: Awaitable[Any], limit_seconds: float | None = None
    ) -> Any:
        task = asyncio.ensure_future(operation)
        cutoff = time.monotonic() + limit_seconds if limit_seconds is not None else None
        try:
            while True:
                self.check()
                if cutoff is not None and time.monotonic() >= cutoff:
                    raise TimeoutError
                done, _ = await asyncio.wait({task}, timeout=0.01)
                if done:
                    self.check()
                    if cutoff is not None and time.monotonic() >= cutoff:
                        raise TimeoutError
                    return task.result()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def invoke_verifier(
    verifier: Any, problem: ProblemSpec, candidate: Any, context: SolverContext
) -> VerificationReport:
    async def call() -> Any:
        # Isolate proposal data, including aliases and Json fields, but retain host clients.
        proposal = (
            validate_model(type(candidate), candidate)
            if isinstance(candidate, BaseModel)
            else deepcopy(candidate)
        )
        if inspect.iscoroutinefunction(verifier):
            return await verifier(problem, proposal, context)
        result = await asyncio.to_thread(verifier, problem, proposal, context)
        return await result if inspect.isawaitable(result) else result

    try:
        raw = await context.controlled(call(), context._limits.verifier_timeout_seconds)
        report = VerificationReport.model_validate(
            raw.model_dump() if isinstance(raw, BaseModel) else raw
        )
        if candidate is None and report.verdict == "verified":
            return report.model_copy(
                update={"verdict": "inconclusive", "reason": "No verifiable candidate produced"}
            )
        return report.validated_for(problem)
    except ExecutionError:
        raise
    except (TimeoutError, SolverStopped):
        return VerificationReport(
            verdict="inconclusive", reason="Verification timed out or budget expired"
        )
    except Exception:
        return VerificationReport(verdict="inconclusive", reason="Verifier failed")
