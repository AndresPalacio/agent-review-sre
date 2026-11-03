"""Native DeepAgents assembly. Imported only when building a solver."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from typing import Any, NotRequired, cast

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.graph import DeepAgentState
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.summarization import create_summarization_middleware
from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.types import AgentMiddleware, hook_config
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from .contracts import (
    SolverContext,
    SolverLimits,
    SolverResult,
    SolverStopped,
    VerificationReport,
    invoke_verifier,
)
from .summarization import bounded_summary_model

_active: ContextVar[SolverContext | None] = ContextVar("solver_context", default=None)


class SolverState(DeepAgentState):
    solver_result: NotRequired[Any]


def _context(runtime: Any) -> SolverContext:
    context = runtime.context if isinstance(runtime.context, SolverContext) else _active.get()
    if context is None:
        raise ValueError("Pass a fresh SolverContext as native graph context")
    return context


def _result(
    context: SolverContext, candidate: Any = None, report: VerificationReport | None = None
) -> dict[str, Any]:
    result = SolverResult(
        candidate=candidate,
        verification=report
        or VerificationReport(
            verdict="inconclusive",
            reason=context._stop_reason or "No verifiable candidate produced",
        ),
        attempts=context._attempts,
        usage=context.usage(),
    )
    context.emit("usage", **result.usage.model_dump())
    return {"solver_result": result}


def _has_pending_tools(state: Any) -> bool:
    acknowledged = set()
    for message in reversed(state["messages"]):
        if isinstance(message, ToolMessage):
            acknowledged.add(message.tool_call_id)
        elif isinstance(message, AIMessage):
            return any(call["id"] not in acknowledged for call in message.tool_calls)
    return False


def _is_task_tool(tool: Any) -> bool:
    if isinstance(tool, Mapping):
        schema = tool.get("function", tool)
        return isinstance(schema, Mapping) and schema.get("name", schema.get("title")) == "task"
    return bool(getattr(tool, "name", getattr(tool, "__name__", None)) == "task")


class _BoundedFilesystem(FilesystemMiddleware):
    @property
    def name(self) -> str:
        return "FilesystemMiddleware"

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        context = _context(request.runtime)
        try:
            return await context.controlled(super().awrap_model_call(request, handler))
        except SolverStopped:
            return AIMessage(content="Solver execution deadline reached.")

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        context = _context(request.runtime)
        try:
            return await context.controlled(super().awrap_tool_call(request, handler))
        except SolverStopped:
            return ToolMessage(
                content="Solver execution deadline reached.", tool_call_id=request.tool_call["id"]
            )


class _BoundedSkills(SkillsMiddleware):
    @property
    def name(self) -> str:
        return "SkillsMiddleware"

    async def abefore_agent(self, state: Any, runtime: Any, config: Any = None) -> Any:
        try:
            return await _context(runtime).controlled(super().abefore_agent(state, runtime, config))
        except SolverStopped:
            return None

    async def abefore_model(self, state: Any, runtime: Any) -> Any:
        try:
            return await _context(runtime).controlled(super().abefore_model(state, runtime))
        except SolverStopped:
            return None


class _Guard(AgentMiddleware[Any, Any, Any]):
    """Execution reservations are shared by every parent and child call."""

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        context = _context(request.runtime)
        try:
            context.reserve("model_calls")
            response = await context.controlled(handler(request))
            for message in getattr(response, "result", []):
                usage = getattr(message, "usage_metadata", None)
                context.record_tokens(usage)
            return response
        except SolverStopped:
            return AIMessage(content="Solver execution budget exhausted.")

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        context = _context(request.runtime)
        delegated = request.tool_call["name"] == "task"
        acquired = False
        token = _active.set(context)
        try:
            context.reserve("tool_calls")
            if delegated:
                # Nonblocking atomic reservation; do not start excess specialists.
                with context._lock:
                    if context._specialists >= context._limits.max_concurrent_specialists:
                        return ToolMessage(
                            content="Specialist concurrency limit reached; defer this task.",
                            tool_call_id=request.tool_call["id"],
                        )
                    context._specialists += 1
                    acquired = True
            context.emit("tool", name=request.tool_call["name"])
            return await context.controlled(handler(request))
        except SolverStopped:
            return ToolMessage(
                content="Solver execution budget exhausted.", tool_call_id=request.tool_call["id"]
            )
        finally:
            if acquired:
                with context._lock:
                    context._specialists -= 1
            _active.reset(token)


class _Verification(AgentMiddleware[Any, Any, Any]):
    state_schema = SolverState

    def __init__(self, verifier: Any, limits: SolverLimits, observer: Any) -> None:
        self.verifier = verifier
        self.limits = limits
        self.observer = observer

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any]:
        context = _context(runtime)
        context.start(self.limits, self.observer)
        return {
            "messages": [
                HumanMessage(
                    content="Authoritative host problem and acceptance criteria:\n"
                    + context.problem.model_dump_json()
                )
            ]
        }

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        context = _context(runtime)
        try:
            context.check()
            if context._counts.get("model_calls", 0) >= self.limits.max_model_calls:
                context._stop_reason = "model_calls_exhausted"
                raise SolverStopped(context._stop_reason)
        except SolverStopped:
            return {**_result(context), "jump_to": "end"}
        return None

    @hook_config(can_jump_to=["model", "end"])
    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        context = _context(runtime)
        if context._stop_reason:
            return {**_result(context), "jump_to": "end"}
        candidate = state.get("structured_response")
        if candidate is None or _has_pending_tools(state):
            # Native routing executes same-turn actions before the retained proposal is checked.
            return None
        return {"jump_to": "end", **await self._verify_candidate(context, candidate)}

    async def _verify_candidate(self, context: SolverContext, candidate: Any) -> dict[str, Any]:
        context._attempts += 1
        report = await invoke_verifier(self.verifier, context.problem, candidate, context)
        context.emit("verification", verdict=report.verdict, attempt=context._attempts)
        if report.verdict == "failed" and context._attempts <= self.limits.max_repairs:
            return {
                "structured_response": None,
                "solver_result": None,
                "messages": [
                    HumanMessage(
                        content=(
                            "Host verification rejected this candidate. "
                            "Repair the actual environment and submit a new candidate.\n"
                        )
                        + report.model_dump_json()
                    )
                ],
                "jump_to": "model",
            }
        return _result(context, candidate, report)

    @hook_config(can_jump_to=["model"])
    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any]:
        context = _context(runtime)
        result = state.get("solver_result")
        if result is None:
            if context._stop_reason:
                return _result(context)
            candidate = state.get("structured_response")
            if candidate is None:
                report = await invoke_verifier(self.verifier, context.problem, None, context)
                context.emit("verification", verdict=report.verdict, final=True)
                return _result(context, report=report)
            update = await self._verify_candidate(context, candidate)
            if update.get("jump_to") == "model":
                return update
            result = update["solver_result"]
        # The final host observation closes the gap after earlier model checks.
        if result.verified:
            report = await invoke_verifier(
                self.verifier, context.problem, result.candidate, context
            )
            context.emit("verification", verdict=report.verdict, final=True)
            return _result(context, result.candidate, report)
        return _result(context, result.candidate, result.verification)


class _SummaryScope(AgentMiddleware[Any, Any, Any]):
    """Replace the native summary slot, retaining its engine and state schema."""

    @property
    def name(self) -> str:
        return "SummarizationMiddleware"

    def __init__(self, native: Any) -> None:
        self.native = native
        self.state_schema = native.state_schema

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        context = _context(request.runtime)
        token = _active.set(context)
        try:
            return await context.controlled(self.native.awrap_model_call(request, handler))
        except SolverStopped:
            return AIMessage(content="Solver summary budget exhausted.")
        finally:
            _active.reset(token)


def _summary_middleware(model: Any, backend: Any) -> _SummaryScope:
    def summary_context() -> SolverContext:
        context = _active.get()
        if context is None:
            raise ValueError("Summary requires active solver context")
        return context

    native = create_summarization_middleware(
        model, backend, summarizer=bounded_summary_model(model, summary_context)
    )
    return _SummaryScope(native)


def build_native(
    *,
    model: Any,
    tools: Sequence[Any],
    solution_schema: type[BaseModel],
    verifier: Callable[..., Any],
    limits: SolverLimits,
    specialists: Sequence[Mapping[str, Any]],
    skills: list[str] | None,
    backend: Any,
    observer: Any,
) -> Any:
    if model is None:
        raise ValueError("An explicit host model is required")
    if any(_is_task_tool(tool) for tool in tools):
        raise ValueError("Tool name 'task' is reserved for bounded native delegation")
    backend = backend if backend is not None else StateBackend()
    guard = _Guard()
    summaries = _summary_middleware(model, backend)
    specs = []
    for specification in specialists:
        spec = dict(specification)
        allowed = {
            "name",
            "description",
            "model",
            "tools",
            "system_prompt",
            "skills",
            "response_format",
        }
        if set(spec) - allowed:
            raise ValueError("Only declarative isolated specialists can share enforced budgets")
        if spec.get("middleware") or spec.get("interrupt_on"):
            raise ValueError("Specialist middleware and pauses are unsupported")
        child_model = spec.get("model", model)
        spec["model"] = child_model
        spec["tools"] = list(spec.get("tools", []))
        if any(_is_task_tool(tool) for tool in spec["tools"]):
            raise ValueError("Recursive specialist delegation is unsupported")
        child_stack = [
            guard,
            _summary_middleware(child_model, backend),
            TodoListMiddleware(),
            _BoundedFilesystem(backend=backend),
        ]
        if spec.get("skills"):
            child_stack.append(_BoundedSkills(backend=backend, sources=spec["skills"]))
        if spec["name"] == "verifier":
            if any(
                (getattr(tool, "metadata", None) or {}).get("read_only") is not True
                for tool in spec["tools"]
            ):
                raise ValueError(
                    "Verifier specialist tools require host-declared metadata read_only=True"
                )
            child_stack = [
                item for item in child_stack if not isinstance(item, FilesystemMiddleware)
            ]
            child_stack.append(
                _BoundedFilesystem(backend=backend, tools=["ls", "read_file", "glob", "grep"])
            )
        spec["middleware"] = child_stack
        specs.append(spec)
    if not any(spec["name"] == "general-purpose" for spec in specs):
        default_stack = [
            guard,
            summaries,
            TodoListMiddleware(),
            _BoundedFilesystem(backend=backend),
        ]
        if skills:
            default_stack.append(_BoundedSkills(backend=backend, sources=skills))
        specs.append(
            {
                "name": "general-purpose",
                "description": "Bounded specialist for delegated work.",
                "model": model,
                "tools": list(tools),
                "skills": skills,
                "middleware": default_stack,
            }
        )
    main_stack = [
        summaries,
        guard,
        TodoListMiddleware(),
        _Verification(verifier, limits, observer),
        _BoundedFilesystem(backend=backend),
    ]
    if skills:
        main_stack.append(_BoundedSkills(backend=backend, sources=skills))
    return create_deep_agent(
        model=model,
        tools=list(tools),
        response_format=ToolStrategy(solution_schema),
        backend=backend,
        skills=skills,
        subagents=cast(Any, specs),
        context_schema=SolverContext,
        state_schema=SolverState,
        middleware=main_stack,
        system_prompt=(
            "Execute the requested solution using available tools. "
            "Only the host verifier can approve success. Preserve acceptance criteria; "
            "report missing information or capabilities honestly. "
            "Never treat a claimed result as proof."
        ),
    ).with_config({"recursion_limit": 1000})
