"""Execution contracts for equity scenarios and deterministic grading of a run's trace.

The contract says what a sound analysis does, whatever the model: the planner runs before the
analysts and the synthesizer after them, each analyst the horizon calls for runs, and each
analyst's compute tool succeeds, called by that analyst for every ticker with the scenario's
date. A model step that fails and falls back still satisfies "runs": whether its answer was
right is what the quality criteria measure.
The grader reads the harness trace (agent and tool start/end events) and lists every breach.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fin_graph.agents.analysis.catalog import ANALYSTS
from fin_graph.domain.analysis import HORIZON_ANALYSTS
from fin_graph.domain.requests.model import Horizon
from fin_graph.evals.market.scenarios import EquityScenario
from fin_graph.integrations.analytics.tools import ANALYTICS_TOOLS


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ToolRequirement(ContractModel):
    name: str
    agent: str
    min_calls: int = Field(ge=0)
    max_calls: int = Field(default=16, ge=1, le=128)
    tickers: tuple[str, ...] = ()
    as_of: str | None = None


class AnalysisContract(ContractModel):
    version: Literal[1] = 1
    required_agents: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    tools: tuple[ToolRequirement, ...]
    precedence: tuple[tuple[str, str], ...] = ()
    max_tool_calls: int = Field(default=96, ge=1, le=512)
    max_agent_calls: int = Field(default=48, ge=1, le=256)
    max_identical_calls: int = Field(default=3, ge=1, le=20)
    max_tool_errors: int = Field(default=1, ge=0, le=20)
    max_planner_calls: int = Field(default=2, ge=1, le=20)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()


def contract_for(scenario: EquityScenario) -> AnalysisContract:
    """The fixed requirements of ``scenario``, from its horizon, tickers and date."""
    analysts = HORIZON_ANALYSTS[Horizon(scenario.metadata.get("horizon", "medium"))]
    tickers = tuple(scenario.tickers)
    agents = tuple(f"analyst_{domain.value}" for domain in analysts)
    return AnalysisContract(
        required_agents=("planner", *agents, "synthesizer"),
        allowed_tools=tuple(sorted(tool.name for tool in ANALYTICS_TOOLS)),
        tools=tuple(
            ToolRequirement(
                name=ANALYSTS[domain].compute_tool,
                agent=f"analyst_{domain.value}",
                min_calls=len(tickers),
                max_calls=len(tickers) * 3,
                tickers=tickers,
                as_of=scenario.as_of.isoformat(),
            )
            for domain in analysts
        ),
        precedence=tuple(("agent:planner", f"agent:{agent}") for agent in agents)
        + tuple((f"agent:{agent}", "agent:synthesizer") for agent in agents),
        max_planner_calls=max(1, scenario.answer.max_waves),
    )


def grade_trajectory(contract: AnalysisContract, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Every breach of ``contract`` in the trace; ``passed`` when there is none."""
    failures: set[str] = set()
    starts: dict[str, dict[str, Any]] = {}
    ends: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events):
        kind, call_id = event.get("kind", ""), event.get("call_id")
        if event.get("seq") != index or not isinstance(call_id, str):
            failures.add("invalid_trace")
            continue
        if kind in {"agent_start", "tool_start"}:
            if call_id in starts:
                failures.add("duplicate_call_id")
            starts[call_id] = event
        elif kind in {"agent_end", "tool_end"}:
            start = starts.get(call_id)
            if not start or call_id in ends or start.get("name") != event.get("name"):
                failures.add("invalid_trace")
            ends[call_id] = event
        else:
            failures.add("invalid_trace")
    if not events:
        failures.add("missing_trace")
    if set(starts) != set(ends):
        failures.add("incomplete_call")
    agents = [event for event in starts.values() if event["kind"] == "agent_start"]
    tools = [event for event in starts.values() if event["kind"] == "tool_start"]
    succeeded = {key for key, event in ends.items() if event.get("status") == "ok"}
    # A model step may fail and fall back by design; it still has to run, in order.
    finished = set(ends)
    for name in contract.required_agents:
        if not any(event["name"] == name and event["call_id"] in finished for event in agents):
            failures.add(f"required_agent:{name}")
    for event in tools:
        if event["name"] not in contract.allowed_tools:
            failures.add(f"unallowed_tool:{event['name']}")
        parent = starts.get(str(event.get("parent", "")))
        if not parent or not str(parent.get("name", "")).startswith("analyst_"):
            failures.add(f"tool_owner:{event['name']}")
    for rule in contract.tools:
        calls = [event for event in tools if event["name"] == rule.name]
        if sum(event["call_id"] in succeeded for event in calls) < rule.min_calls:
            failures.add(f"required_tool:{rule.name}")
        if len(calls) > rule.max_calls:
            failures.add(f"tool_count:{rule.name}")
        for event in calls:
            if starts.get(str(event.get("parent", "")), {}).get("name") != rule.agent:
                failures.add(f"tool_owner:{rule.name}")
            args = event.get("args") or {}
            if rule.tickers and str(args.get("ticker", "")).upper() not in rule.tickers:
                failures.add(f"arguments:{rule.name}:ticker")
            if rule.as_of and str(args.get("as_of", ""))[:10] != rule.as_of:
                failures.add(f"arguments:{rule.name}:as_of")
    for before, after in contract.precedence:
        before_name, after_name = before.partition(":")[2], after.partition(":")[2]
        for event in agents:
            if event["name"] != after_name:
                continue
            if not any(
                end["name"] == before_name and end["seq"] < event["seq"] for end in ends.values()
            ):
                failures.add(f"precedence:{before}->{after}")
    errors = sum(
        event.get("status") == "error" and event["kind"] == "tool_end" for event in ends.values()
    )
    duplicates = Counter(
        (event["name"], json.dumps(event.get("args", {}), sort_keys=True, default=str))
        for event in tools
    )
    if len(tools) > contract.max_tool_calls:
        failures.add("tool_budget")
    if len(agents) > contract.max_agent_calls:
        failures.add("agent_budget")
    if sum(event["name"] == "planner" for event in agents) > contract.max_planner_calls:
        failures.add("planning_budget")
    if errors > contract.max_tool_errors:
        failures.add("tool_error_budget")
    if max(duplicates.values(), default=0) > contract.max_identical_calls:
        failures.add("duplicate_tool_budget")
    return {
        "passed": not failures,
        "failures": sorted(failures),
        "tool_calls": len(tools),
        "agent_calls": len(agents),
        "tool_errors": errors,
        "contract_fingerprint": contract.fingerprint,
    }
