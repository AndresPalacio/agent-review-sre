"""Immutable execution contracts and deterministic trajectory grading."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fin_graph.domain.tools import read_tool_meta
from fin_graph.domain.window import IncidentWindow
from fin_graph.evals.fixtures import SOURCE_TO_TOOL, FixtureBackend
from fin_graph.evals.scenarios import Scenario
from fin_graph.evals.scoring import TRAJECTORY_TO_TOOL


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ArgumentRule(ContractModel):
    path: str = Field(min_length=1)
    op: Literal["equals", "contains", "one_of", "gte", "lte", "exists"] = "equals"
    value: Any = None

    def matches(self, args: dict[str, Any]) -> bool:
        observed: Any = args
        for part in self.path.split("."):
            if not isinstance(observed, dict) or part not in observed:
                return False
            observed = observed[part]
        try:
            match self.op:
                case "exists":
                    return observed is not None
                case "equals":
                    return bool(observed == self.value)
                case "contains":
                    return self.value in observed
                case "one_of":
                    return observed in self.value
                case "gte":
                    return bool(observed >= self.value)
                case "lte":
                    return bool(observed <= self.value)
        except (TypeError, ValueError):
            return False


class ToolRequirement(ContractModel):
    name: str
    agent: str | None = None
    allowed_agents: tuple[str, ...] = ()
    min_calls: int = Field(default=1, ge=0)
    max_calls: int = Field(default=16, ge=1, le=128)
    arguments: tuple[ArgumentRule, ...] = ()

    @model_validator(mode="after")
    def valid_counts(self) -> ToolRequirement:
        if self.min_calls > self.max_calls:
            raise ValueError("min_calls exceeds max_calls")
        return self


class TrajectoryContract(ContractModel):
    version: Literal[1] = 1
    required_agents: tuple[str, ...] = ()
    required_agent_groups: tuple[tuple[str, ...], ...] = ()
    allowed_tools: tuple[str, ...]
    tools: tuple[ToolRequirement, ...]
    precedence: tuple[tuple[str, str], ...] = ()
    max_tool_calls: int = Field(default=64, ge=1, le=256)
    max_agent_calls: int = Field(default=40, ge=1, le=256)
    max_identical_calls: int = Field(default=3, ge=1, le=20)
    max_tool_errors: int = Field(default=2, ge=0, le=20)
    max_planner_calls: int = Field(default=3, ge=1, le=20)
    enforce_delegation_window: bool = False

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()


def contract_for(scenario: Scenario) -> TrajectoryContract:
    """Derive reviewed requirements from the frozen reference and registered tool metadata."""
    backend = FixtureBackend(scenario)
    available = {tool.name: tool for tool in backend.registry.tools(backend.context)}
    required = {SOURCE_TO_TOOL[source] for source in scenario.answer.required_evidence_sources}
    required.update(TRAJECTORY_TO_TOOL[query] for query in scenario.answer.required_queries)
    if not required:
        required.add("k8s_list_pods" if scenario.suite == "eks" else "aws_metric_data")
    rules = []
    for name in sorted(available):
        meta = read_tool_meta(available[name])
        owner = (
            f"specialist_{next(iter(meta.domains)).value}"
            if meta and len(meta.domains) == 1
            else None
        )
        arguments = []
        schema = available[name].args
        if "start" in schema and "end" in schema:
            arguments.extend(
                [
                    ArgumentRule(path="start", op="exists"),
                    ArgumentRule(path="end", op="exists"),
                ]
            )
        identifier = scenario.metadata.get("db_instance_identifier")
        if identifier and name in {"aws_rds_events", "aws_rds_performance"}:
            field = "identifier" if name == "aws_rds_events" else "resource_id"
            arguments.append(ArgumentRule(path=field, value=identifier))
        if name == "aws_metric_data":
            recorded = scenario.evidence["aws_cloudwatch_metrics"]
            arguments.append(
                ArgumentRule(
                    path="namespace",
                    op="one_of",
                    value=[s.strip() for s in recorded["namespace"].split(",")],
                )
            )
            arguments.append(
                ArgumentRule(
                    path="metric_name",
                    op="one_of",
                    value=sorted({m["metric_name"] for m in recorded["metric_data_results"]}),
                )
            )
        if (
            name.startswith("k8s_")
            and name != "k8s_list_nodes"
            and scenario.metadata.get("namespace")
        ):
            arguments.append(ArgumentRule(path="namespace", value=scenario.metadata["namespace"]))
        rules.append(
            ToolRequirement(
                name=name,
                agent=owner,
                min_calls=int(name in required),
                arguments=tuple(arguments),
                allowed_agents=tuple(sorted(f"specialist_{d.value}" for d in meta.domains))
                if meta
                else (),
            )
        )
    if required - set(available):
        raise ValueError(
            f"required tools unavailable for {scenario.key}: {required - set(available)}"
        )
    owners = (
        ("specialist_kubernetes",)
        if scenario.suite == "eks"
        else ("specialist_metrics", "specialist_cloud")
    )
    specialist = "|".join(owners)
    return TrajectoryContract(
        required_agents=("planner", "synthesizer"),
        required_agent_groups=(owners,),
        enforce_delegation_window=True,
        max_planner_calls=max(1, scenario.answer.max_investigation_loops),
        allowed_tools=tuple(sorted(available)),
        tools=tuple(rules),
        precedence=(
            ("agent:planner", f"agent:{specialist}"),
            (f"agent:{specialist}", "agent:synthesizer"),
            ("agent:synthesizer", "agent:judge"),
        ),
    )


def grade_trajectory(contract: TrajectoryContract, events: list[dict[str, Any]]) -> dict[str, Any]:
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
            if (
                not start
                or call_id in ends
                or start.get("name") != event.get("name")
                or start["kind"].split("_")[0] != kind.split("_")[0]
                or event.get("status") not in {"ok", "error"}
            ):
                failures.add("invalid_trace")
            ends[call_id] = event
        else:
            failures.add("invalid_trace")
    if not events:
        failures.add("missing_trace")
    if set(starts) != set(ends):
        failures.add("incomplete_call")
    agents = [e for e in starts.values() if e["kind"] == "agent_start"]
    tools = [e for e in starts.values() if e["kind"] == "tool_start"]
    successful = {key for key, e in ends.items() if e.get("status") == "ok"}
    for name in contract.required_agents:
        if not any(e["name"] == name and e["call_id"] in successful for e in agents):
            failures.add(f"required_agent:{name}")
    for group in contract.required_agent_groups:
        if not any(e["name"] in group and e["call_id"] in successful for e in agents):
            failures.add(f"required_agent:{'|'.join(group)}")
    for event in agents:
        if ends.get(event["call_id"], {}).get("status") == "error":
            failures.add(f"agent_error:{event['name']}")
    for event in tools:
        if event["name"] not in contract.allowed_tools:
            failures.add(f"unallowed_tool:{event['name']}")
        parent = starts.get(str(event.get("parent", "")))
        if (
            not parent
            or parent["kind"] != "agent_start"
            or not parent["name"].startswith("specialist_")
        ):
            failures.add(f"tool_owner:{event['name']}")
        elif parent["seq"] >= event["seq"] or ends.get(parent["call_id"], {}).get(
            "seq", -1
        ) <= ends.get(event["call_id"], {}).get("seq", float("inf")):
            failures.add(f"tool_scope:{event['name']}")
        args = event.get("args", {})
        if contract.enforce_delegation_window and "start" in args and "end" in args:
            try:
                window = IncidentWindow.model_validate((parent or {}).get("window"))
                if not (
                    int(window.since.timestamp())
                    <= args["start"]
                    < args["end"]
                    <= int(window.until.timestamp())
                ):
                    failures.add(f"arguments:{event['name']}:window")
            except (ValueError, TypeError):
                failures.add(f"arguments:{event['name']}:window")
        if (
            "start" in args
            and "end" in args
            and (
                not isinstance(args["start"], (int, float))
                or not isinstance(args["end"], (int, float))
                or args["start"] >= args["end"]
            )
        ):
            failures.add(f"arguments:{event['name']}:window")
    for rule in contract.tools:
        calls = [e for e in tools if e["name"] == rule.name]
        if sum(e["call_id"] in successful for e in calls) < rule.min_calls:
            failures.add(f"required_tool:{rule.name}")
        if len(calls) > rule.max_calls:
            failures.add(f"tool_count:{rule.name}")
        for event in calls:
            observed_agent = starts.get(str(event.get("parent", "")), {}).get("name")
            if rule.allowed_agents and observed_agent not in rule.allowed_agents:
                failures.add(f"tool_owner:{rule.name}")
            if (
                rule.agent
                and starts.get(str(event.get("parent", "")), {}).get("name") != rule.agent
            ):
                failures.add(f"tool_owner:{rule.name}")
            for arg in rule.arguments:
                if not arg.matches(event.get("args", {})):
                    failures.add(f"arguments:{rule.name}:{arg.path}")
    for before, after in contract.precedence:

        def matches(event: dict[str, Any], selector: str) -> bool:
            kind, _, name = selector.partition(":")
            return bool(
                event["name"] in (name or kind).split("|")
                and event["kind"].startswith(kind if name else "tool")
            )

        for event in starts.values():
            if matches(event, after) and not any(
                matches(end, before) and end.get("status") == "ok" and end["seq"] < event["seq"]
                for end in ends.values()
            ):
                failures.add(f"precedence:{before}->{after}")
    errors = sum(e.get("status") == "error" and e["kind"] == "tool_end" for e in ends.values())
    duplicates = Counter((e["name"], json.dumps(e.get("args", {}), sort_keys=True)) for e in tools)
    if len(tools) > contract.max_tool_calls:
        failures.add("tool_budget")
    if len(agents) > contract.max_agent_calls:
        failures.add("agent_budget")
    if sum(e["name"] == "planner" for e in agents) > contract.max_planner_calls:
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
