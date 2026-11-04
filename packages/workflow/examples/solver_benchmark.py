"""Paired, offline solver contracts; optional provider runs require an explicit flag.

Run from ``packages/workflow``::

    uv run --extra agents python examples/solver_benchmark.py

These scripted cases measure execution and verification contracts, not model reasoning.
For a reasoning experiment supply ``--model-factory your_module:create_model``. The
factory must take no arguments and return a fresh BaseChatModel. That flag explicitly
opts into its provider calls/costs; the benchmark never chooses a provider itself.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import importlib
import io
import json
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.tools import BaseTool, tool
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, PrivateAttr

from bm_workflow.agents import (
    ProblemSpec,
    SolverContext,
    SolverLimits,
    VerificationCheck,
    VerificationReport,
    build_solver_agent,
)


class Solution(BaseModel):
    """A candidate assertion; the host still has to check the resulting environment."""

    model_config = ConfigDict(extra="forbid")
    resolved: bool
    summary: str
    evidence_refs: list[str] = []


if __package__:
    from .solver_cases import Case, cases
else:
    from solver_cases import Case, cases


@dataclass
class LocalEnvironment:
    """Per-arm local documents; failed writes cause no effects and are never auto-retried."""

    case: Case
    documents: dict[str, str] = field(init=False)
    effects: list[tuple[str, str]] = field(default_factory=list)
    tool_failures: int = 0
    verification_failures: int = 0
    incorrect_effects: int = 0
    _write_attempts: int = 0

    def __post_init__(self) -> None:
        self.documents = self.case.initial_documents
        if self.case.scenario == "missing_data":
            self.documents.pop(self.case.missing_document)

    def tools(self) -> list[BaseTool]:
        @tool
        def read_document(path: str) -> str:
            """Read one local document; returns JSON containing ok and content or error."""
            if path not in self.documents:
                return json.dumps({"ok": False, "error": "required input document is missing"})
            return json.dumps({"ok": True, "content": self.documents[path]})

        @tool
        def write_document(path: str, content: str) -> str:
            """Write a local document; check ok before claiming the write succeeded."""
            self._write_attempts += 1
            if path != self.case.destination:
                self.tool_failures += 1
                return json.dumps({"ok": False, "error": "path is outside this task's write scope"})
            if self.case.scenario == "tool_failure" and self._write_attempts == 1:
                self.tool_failures += 1
                return json.dumps(
                    {"ok": False, "error": "temporary local storage failure; retry allowed"}
                )
            self.documents[path] = content
            self.effects.append((path, content))
            # Observe every completed write in either arm, without feeding the
            # oracle back to the model. The initially unsolved state is not recovery.
            if not grade_environment(self.case, self).passed:
                self.incorrect_effects += 1
            return json.dumps({"ok": True, "evidence_ref": path})

        return (
            [read_document]
            if self.case.scenario == "missing_tool"
            else [read_document, write_document]
        )

    @property
    def duplicate_effects(self) -> int:
        return sum(count - 1 for count in Counter(self.effects).values())

    def verify(self, problem: ProblemSpec, candidate: Any, context: Any) -> VerificationReport:
        """Host acceptance checks inspect documents, never candidate evidence references."""
        missing = [path for path in self.case.initial_documents if path not in self.documents]
        if self.case.scenario == "missing_tool":
            missing.append("write_document capability")
        if missing:
            return VerificationReport(
                verdict="blocked",
                missing_requirements=tuple(missing),
                reason="Required input or host capability is unavailable.",
            )
        try:
            content = self.documents.get(self.case.destination, "")
            if self.case.family == "configuration":
                config = json.loads(content)
                primary = (
                    type(config.get("port")) is int
                    and type(config.get("retries")) is int
                    and config["port"] == 8443
                    and config["retries"] == 3
                )
                preserved = config.get("log_level") == "INFO"
            elif self.case.family == "transformation":
                expected: dict[str, int] = {}
                for row in csv.DictReader(io.StringIO(self.case.initial_source)):
                    if row["status"] == "paid":
                        expected[row["region"]] = expected.get(row["region"], 0) + (
                            int(row["quantity"]) * int(row["unit_price"])
                        )
                primary = json.loads(content) == expected
                preserved = self.documents.get(self.case.source) == self.case.initial_source
            elif self.case.family == "research":
                inputs = self.case.initial_documents
                manifest = json.loads(inputs[self.case.source])
                stocks = json.loads(inputs[manifest["universe"]])["stocks"]
                scores = json.loads(inputs[manifest["fundamentals"]])["quality_scores"]
                threshold = json.loads(inputs[manifest["policy"]])["minimum_quality"]
                watchlist = []
                for stock in sorted(stocks, key=lambda stock: stock["ticker"]):
                    ticker = stock["ticker"]
                    if stock["active"] and scores[ticker] > threshold:
                        watchlist.append({"ticker": ticker, "sector": stock["sector"]})
                primary = json.loads(content) == {"watchlist": watchlist}
                preserved = all(self.documents.get(path) == value for path, value in inputs.items())
            else:
                primary = content == self.case.initial_source.replace(
                    "RELEASE_PENDING", "Version 2.0"
                )
                preserved = content.splitlines()[::2] == self.case.initial_source.splitlines()[::2]
        except (ValueError, TypeError, AttributeError, KeyError):
            primary, preserved = False, False
        if not (primary and preserved):
            self.verification_failures += 1
        criterion = next(iter(problem.acceptance_criteria))
        checks = (
            VerificationCheck(
                criterion_id=criterion,
                passed=primary,
                detail="Checked the actual output document.",
                evidence_refs=(self.case.destination,),
            ),
            VerificationCheck(
                criterion_id="preservation",
                passed=preserved,
                detail="Checked preserved input fields/text.",
                evidence_refs=(self.case.source,),
            ),
        )
        snapshot = hashlib.sha256(json.dumps(self.documents, sort_keys=True).encode()).hexdigest()
        return VerificationReport(
            verdict="verified" if primary and preserved else "failed",
            checks=checks,
            snapshot_digest=snapshot,
            reason="Environment satisfies acceptance criteria."
            if primary and preserved
            else "Output is incorrect.",
        )


@dataclass(frozen=True)
class Grade:
    passed: bool
    detail: str


def grade_environment(case: Case, environment: LocalEnvironment, candidate: Any = None) -> Grade:
    """Independent final oracle with hand-derived expectations, ignoring all model claims.

    This deliberately does not call the host verifier or its calculation helper. A
    false success means a success claim survives while this final environment fails.
    """
    try:
        output = environment.documents.get(case.destination, "")
        if case.family == "configuration":
            parsed = json.loads(output)
            passed = (
                type(parsed.get("port")) is int
                and type(parsed.get("retries")) is int
                and (parsed["port"], parsed["retries"], parsed.get("log_level"))
                == (8443, 3, "INFO")
            )
        elif case.family == "transformation":
            expected = {"west": 28, "east": 6} if case.variant else {"east": 20, "west": 12}
            passed = (
                json.loads(output) == expected
                and environment.documents.get(case.source) == case.initial_source
            )
        elif case.family == "research":
            # Hand-derived fixture answers, independent of host/model selection logic.
            watchlist = (
                [
                    {"ticker": "AAPL", "sector": "technology"},
                    {"ticker": "MSFT", "sector": "technology"},
                ]
                if case.variant
                else [{"ticker": "AAPL", "sector": "technology"}]
            )
            passed = json.loads(output) == {"watchlist": watchlist} and all(
                environment.documents.get(path) == content
                for path, content in case.initial_documents.items()
            )
        else:
            owner = "QA" if case.variant else "Operations"
            passed = output == f"Project Atlas\nVersion 2.0\nOwner: {owner}\n"
    except (ValueError, TypeError, AttributeError):
        passed = False
    return Grade(
        passed, "Final documents satisfy oracle." if passed else "Final documents fail oracle."
    )


class ScriptedModel(BaseChatModel):
    """Offline policy fixture; native LangChain/DeepAgents still executes every tool."""

    case: Case
    _turn: int = PrivateAttr(default=0)
    _source: str = PrivateAttr(default="")
    _observations: dict[str, str] = PrivateAttr(default_factory=dict)
    _pending_path: str = PrivateAttr(default="")
    _missing_input: bool = PrivateAttr(default=False)
    _solution_tool: str = PrivateAttr(default="Solution")

    @property
    def _llm_type(self) -> str:
        return "offline-solver-contract-script"

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any) -> Any:
        # ToolStrategy publishes the solution schema as a function tool.
        for item in tools:
            if isinstance(item, dict):
                name = item.get("function", item).get("name")
            else:
                name = getattr(item, "name", getattr(item, "__name__", None))
            if name == "Solution":
                self._solution_tool = name
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        turn = self._turn
        self._turn += 1
        scenario = self.case.scenario
        last_tool = next(
            (message for message in reversed(messages) if isinstance(message, ToolMessage)), None
        )
        if last_tool is not None and last_tool.name == "read_document":
            observed = json.loads(str(last_tool.content))
            self._source = observed.get("content", "")
            if observed.get("ok"):
                self._observations[self._pending_path] = self._source
            else:
                self._missing_input = True

        def response(name: str, args: dict[str, Any]) -> ChatResult:
            if name == "read_document":
                self._pending_path = args["path"]
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[{"name": name, "args": args, "id": f"script-{turn}"}],
                        )
                    )
                ]
            )

        def final(resolved: bool = True) -> ChatResult:
            return response(
                self._solution_tool,
                {
                    "resolved": resolved,
                    "summary": "Local changes completed."
                    if resolved
                    else "Required input/capability unavailable.",
                    "evidence_refs": [
                        self.case.destination + ("#forged" if scenario == "forged_evidence" else "")
                    ],
                },
            )

        if scenario == "forged_evidence":
            return final()
        if scenario == "missing_tool":
            return final(False)
        if turn == 0:
            return response("read_document", {"path": self.case.source})
        if self.case.family == "research":
            if self._missing_input:
                return final(False)
            # Only observed tool contents determine lookup paths and the answer.
            manifest = json.loads(self._observations[self.case.source])
            for path in manifest.values():
                if path not in self._observations:
                    return response("read_document", {"path": path})
            stocks = json.loads(self._observations[manifest["universe"]])["stocks"]
            scores = json.loads(self._observations[manifest["fundamentals"]])["quality_scores"]
            threshold = json.loads(self._observations[manifest["policy"]])["minimum_quality"]
            if any(stock["active"] and stock["ticker"] not in scores for stock in stocks):
                return final(False)
            if (
                last_tool is not None
                and last_tool.name == "write_document"
                and json.loads(str(last_tool.content)).get("ok")
            ):
                return final()
            watchlist = sorted(
                [
                    {"ticker": stock["ticker"], "sector": stock["sector"]}
                    for stock in stocks
                    if stock["active"] and scores[stock["ticker"]] > threshold
                ],
                key=lambda item: item["ticker"],
            )
            return response(
                "write_document",
                {
                    "path": self.case.destination,
                    "content": json.dumps({"watchlist": watchlist}),
                },
            )
        if scenario == "missing_data":
            return final(False)
        wrong = scenario == "wrong_diagnosis" and turn == 1
        should_write = (
            turn == 1
            or (scenario == "tool_failure" and turn == 2)
            or (scenario == "wrong_diagnosis" and turn == 3)
        )
        if should_write:
            if self.case.family == "configuration":
                config = json.loads(self._source)
                config.update(port=443 if wrong else 8443, retries=3)
                content = json.dumps(config)
            elif self.case.family == "transformation":
                totals: dict[str, int] = {}
                for row in csv.DictReader(io.StringIO(self._source)):
                    if row["status"] == "paid":
                        region = row["region"]
                        totals[region] = totals.get(region, 0) + int(row["quantity"]) * int(
                            row["unit_price"]
                        )
                content = json.dumps(totals)
            else:
                content = self._source.replace("RELEASE_PENDING", "Version 2.0")
            return response("write_document", {"path": self.case.destination, "content": content})
        return final()


class UsageMeter(BaseCallbackHandler):
    """Measure actual model/tool execution, including provider token usage when available."""

    def __init__(self) -> None:
        self.model_calls = 0
        self.tool_calls = 0
        self.observed_tokens = 0
        self._calls_with_usage = 0
        self._lock = threading.Lock()

    @property
    def total_tokens(self) -> int | None:
        """A complete total, never a subtotal disguised as the entire cost."""
        with self._lock:
            return self.observed_tokens if self._calls_with_usage == self.model_calls else None

    def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        with self._lock:
            self.model_calls += 1

    def on_tool_start(self, serialized: Any, input_str: str, **kwargs: Any) -> None:
        with self._lock:
            self.tool_calls += 1

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        generations = [generation for batch in response.generations for generation in batch]
        complete = bool(generations)
        with self._lock:
            for generation in generations:
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
                tokens = usage.get("total_tokens") if usage else None
                if type(tokens) is int and tokens >= 0:
                    self.observed_tokens += tokens
                else:
                    complete = False
            self._calls_with_usage += int(complete)


class BudgetExhausted(RuntimeError):
    pass


class BaselineBudget(AgentMiddleware):
    """The baseline receives exactly the same model/tool/deadline allowances."""

    def __init__(self, limits: SolverLimits) -> None:
        self.limits = limits
        self._model_calls = 0
        self._tool_calls = 0
        self._lock = asyncio.Lock()

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        async with self._lock:
            if self._model_calls >= self.limits.max_model_calls:
                raise BudgetExhausted("model call allowance exhausted")
            self._model_calls += 1
        return await handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        async with self._lock:
            if self._tool_calls >= self.limits.max_tool_calls:
                raise BudgetExhausted("tool call allowance exhausted")
            self._tool_calls += 1
        return await handler(request)


@dataclass(frozen=True)
class EvaluationRow:
    case_id: str
    family: str
    scenario: str
    split: str
    arm: str
    environment_passed: bool
    claimed_success: bool
    verified: bool
    false_success: bool
    blocked: bool
    recovered: bool
    duplicate_effects: int
    tool_failures: int
    model_calls: int
    tool_calls: int
    summary_calls: int
    total_tokens: int | None
    duration_seconds: float
    over_budget: bool
    resolution: str
    error: str | None = None
    incorrect_effects: int = 0
    total_model_calls: int = 0
    observed_tokens: int = 0
    perturbation_scope: str = "none"


@dataclass(frozen=True)
class EvaluationReport:
    mode: str
    rows: tuple[EvaluationRow, ...]

    def summary(self, *, split: str | None = None) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for arm in ("baseline", "solver"):
            rows = [
                row for row in self.rows if row.arm == arm and (split is None or row.split == split)
            ]
            solved = [row for row in rows if row.environment_passed]
            tokens_known = bool(rows) and all(row.total_tokens is not None for row in rows)
            result[arm] = {
                "cases": len(rows),
                "solved_cases": len(solved),
                "environment_resolution_rate": len(solved) / len(rows) if rows else 0.0,
                "verified_resolution_rate": sum(
                    row.claimed_success and row.environment_passed for row in rows
                )
                / len(rows)
                if rows
                else 0.0,
                "host_verified_resolution_rate": sum(
                    row.verified and row.environment_passed for row in rows
                )
                / len(rows)
                if rows
                else 0.0,
                "false_successes": sum(row.false_success for row in rows),
                "blocked_cases": sum(row.blocked for row in rows),
                "recoveries": sum(row.recovered for row in rows),
                "incorrect_effects": sum(row.incorrect_effects for row in rows),
                "duplicate_effects": sum(row.duplicate_effects for row in rows),
                "over_budget_runs": sum(row.over_budget for row in rows),
                "model_calls": sum(row.model_calls for row in rows),
                "total_model_calls": sum(row.total_model_calls for row in rows),
                "tool_calls": sum(row.tool_calls for row in rows),
                "summary_calls": sum(row.summary_calls for row in rows),
                "duration_seconds": sum(row.duration_seconds for row in rows),
                "total_tokens": sum(row.total_tokens or 0 for row in rows)
                if tokens_known
                else None,
                "observed_tokens": sum(row.observed_tokens for row in rows),
                "token_usage_complete": tokens_known,
                "total_model_calls_per_solved_case": sum(row.total_model_calls for row in rows)
                / len(solved)
                if solved
                else None,
                "model_calls_per_solved_case": sum(row.model_calls for row in rows) / len(solved)
                if solved
                else None,
                "tool_calls_per_solved_case": sum(row.tool_calls for row in rows) / len(solved)
                if solved
                else None,
                "summary_calls_per_solved_case": sum(row.summary_calls for row in rows)
                / len(solved)
                if solved
                else None,
                "duration_seconds_per_solved_case": sum(row.duration_seconds for row in rows)
                / len(solved)
                if solved
                else None,
                "tokens_per_solved_case": sum(row.total_tokens or 0 for row in rows) / len(solved)
                if tokens_known and solved
                else None,
            }
        return result

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "interpretation": (
                "Scripted contract checks do not estimate model reasoning quality or superiority."
                if self.mode == "scripted_contract"
                else "Provider results apply to these cases and budgets; no superiority claim."
            ),
            "summary": self.summary(),
            "by_split": {split: self.summary(split=split) for split in ("contract", "holdout")},
            "comparison": {
                "verified_resolution_rate": (
                    "Independent final environment grade AND success claim, in both arms."
                ),
                "host_verified_resolution_rate": (
                    "Native solver host approval AND independent final grade; "
                    "baseline has no host approval."
                ),
                "affordances": (
                    "Same supplied host tools and agent/tool caps. Solver additionally has native "
                    "planning, filesystem, delegation, host verification/repair and up to 4 "
                    "separately capped summary calls. Baseline has no summaries. Compare "
                    "total_model_calls for all model executions."
                ),
                "recovery": (
                    "Final environment passes after an observed failed tool write or incorrect "
                    "completed write; initial unsolved state does not count."
                ),
                "holdouts": (
                    "Public changed-data fixtures, reported separately; "
                    "not evidence of unseen model generalization."
                ),
                "timing": (
                    "One run per case/arm; duration includes graph construction and execution. "
                    "No statistical latency claim."
                ),
            },
            "rows": [asdict(row) for row in self.rows],
        }


async def _run_arm(
    case: Case,
    arm: str,
    model_factory: Callable[[], BaseChatModel] | None,
    seen_models: list[BaseChatModel] | None = None,
) -> EvaluationRow:
    environment = LocalEnvironment(case)
    model = ScriptedModel(case=case) if model_factory is None else model_factory()
    if not isinstance(model, BaseChatModel):
        raise TypeError("model factory must return a fresh BaseChatModel")
    if seen_models is not None:
        if any(model is previous for previous in seen_models):
            raise ValueError("model factory must return a fresh model for every case and arm")
        seen_models.append(model)
    tools = environment.tools()
    limits = case.limits()
    problem = case.problem()
    meter = UsageMeter()
    config = {"callbacks": [meter], "recursion_limit": 200}
    started = time.perf_counter()
    candidate: Any = None
    verified, blocked = False, False
    resolution, error = "inconclusive", None
    summary_calls = 0
    solver_context = SolverContext(problem=problem, host_context=environment)
    try:
        if arm == "solver":
            graph = build_solver_agent(
                model=model,
                tools=tools,
                solution_schema=Solution,
                verifier=environment.verify,
                limits=limits,
            )
            output = await graph.ainvoke(
                {"messages": [HumanMessage(problem.model_dump_json())]},
                config=config,
                context=solver_context,
            )
            result = output["solver_result"]
            candidate = result.candidate
            verified = result.verified
            resolution = result.verification.verdict
            blocked = resolution == "blocked"
            claimed = verified
        else:
            graph = create_agent(
                model=model,
                tools=tools,
                response_format=ToolStrategy(Solution),
                middleware=[BaselineBudget(limits)],
            )
            async with asyncio.timeout(limits.timeout_seconds):
                output = await graph.ainvoke(
                    {"messages": [HumanMessage(problem.model_dump_json())]},
                    config=config,
                )
            candidate = output.get("structured_response")
            claimed = bool(candidate and candidate.resolved)
            blocked = bool(
                candidate
                and not candidate.resolved
                and case.scenario in {"missing_data", "missing_tool"}
            )
            resolution = "blocked" if blocked else "unverified"
    except (BudgetExhausted, TimeoutError) as exc:
        claimed = False
        resolution = "budget_exhausted" if isinstance(exc, BudgetExhausted) else "timeout"
    except Exception as exc:
        # A failed arm is visible in the report; never convert an execution error to success.
        claimed = False
        # Provider exception bodies can contain prompts, endpoints or credentials.
        error = type(exc).__name__ + ": arm execution failed"
    # Runtime context survives a graph exception, unlike a missing final result.
    if arm == "solver":
        summary_calls = solver_context.usage().summary_calls
    duration = time.perf_counter() - started
    grade = grade_environment(case, environment, candidate)
    model_calls = max(0, meter.model_calls - summary_calls)
    over_budget = (
        model_calls > limits.max_model_calls
        or meter.tool_calls > limits.max_tool_calls
        or summary_calls > limits.max_summary_calls
    )
    return EvaluationRow(
        case_id=case.id,
        family=case.family,
        scenario=case.scenario,
        split=case.split,
        arm=arm,
        environment_passed=grade.passed,
        claimed_success=claimed,
        verified=verified,
        false_success=claimed and not grade.passed,
        blocked=blocked,
        recovered=grade.passed and bool(environment.tool_failures or environment.incorrect_effects),
        duplicate_effects=environment.duplicate_effects,
        tool_failures=environment.tool_failures,
        model_calls=model_calls,
        tool_calls=meter.tool_calls,
        summary_calls=summary_calls,
        total_tokens=meter.total_tokens,
        duration_seconds=duration,
        over_budget=over_budget,
        resolution=resolution,
        error=error,
        incorrect_effects=environment.incorrect_effects,
        total_model_calls=meter.model_calls,
        observed_tokens=meter.observed_tokens,
        perturbation_scope=(
            "scripted_model" if model_factory is None else "scripted_only_not_injected"
        )
        if case.scenario in {"wrong_diagnosis", "forged_evidence"}
        else "none"
        if case.scenario == "direct"
        else "environment_or_budget",
    )


async def run_benchmark(
    selected_cases: Sequence[Case] | None = None,
    *,
    model_factory: Callable[[], BaseChatModel] | None = None,
) -> EvaluationReport:
    """Pair each case with fresh models/documents and identical supplied host tools/budgets."""
    rows = []
    # Strong references prevent object-id reuse and catch state leaking between arms.
    seen_models: list[BaseChatModel] = []
    # Context-local tracing opt-out also makes an offline run safe when the host
    # shell happens to have tracing enabled. Provider calls remain explicit.
    with tracing_context(enabled=False):
        for case in cases() if selected_cases is None else selected_cases:
            for arm in ("baseline", "solver"):
                rows.append(await _run_arm(case, arm, model_factory, seen_models))
    return EvaluationReport(
        "scripted_contract" if model_factory is None else "provider", tuple(rows)
    )


def load_model_factory(reference: str) -> Callable[[], BaseChatModel]:
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("--model-factory must be an import reference module:function")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError("--model-factory must reference a callable")
    return factory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-factory", help="Explicit provider opt-in: module:function returning a fresh model"
    )
    parser.add_argument(
        "--case", action="append", default=[], help="Run only this case ID (repeatable)"
    )
    arguments = parser.parse_args()
    selected = tuple(case for case in cases() if not arguments.case or case.id in arguments.case)
    if not selected or set(arguments.case) - {case.id for case in selected}:
        parser.error("unknown or empty case selection")
    factory = load_model_factory(arguments.model_factory) if arguments.model_factory else None
    report = asyncio.run(run_benchmark(selected, model_factory=factory))
    print(json.dumps(report.as_dict(), indent=2))
    if any(
        row.over_budget or (row.arm == "solver" and row.false_success) or row.error
        for row in report.rows
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
