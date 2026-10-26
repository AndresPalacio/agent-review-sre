from __future__ import annotations

import copy

import pytest


def example():
    return [
        {"seq": 0, "kind": "agent_start", "name": "specialist_kubernetes", "call_id": "a"},
        {
            "seq": 1,
            "kind": "tool_start",
            "name": "pods",
            "call_id": "p",
            "parent": "a",
            "args": {"namespace": "payments"},
        },
        {"seq": 2, "kind": "tool_end", "name": "pods", "call_id": "p", "status": "ok"},
        {"seq": 3, "kind": "tool_start", "name": "logs", "call_id": "l", "parent": "a", "args": {}},
        {"seq": 4, "kind": "tool_end", "name": "logs", "call_id": "l", "status": "ok"},
        {
            "seq": 5,
            "kind": "agent_end",
            "name": "specialist_kubernetes",
            "call_id": "a",
            "status": "ok",
        },
    ]


def contract():
    from fin_graph.evals.trajectory import TrajectoryContract

    return TrajectoryContract.model_validate(
        {
            "required_agents": ["specialist_kubernetes"],
            "allowed_tools": ["pods", "logs"],
            "tools": [
                {
                    "name": "pods",
                    "agent": "specialist_kubernetes",
                    "min_calls": 1,
                    "max_calls": 1,
                    "arguments": [{"path": "namespace", "op": "equals", "value": "payments"}],
                }
            ],
            "precedence": [["pods", "logs"]],
            "max_tool_calls": 2,
        }
    )


def test_observed_trace_satisfies_agent_tool_arguments_and_dependency():
    from fin_graph.evals.trajectory import grade_trajectory

    assert grade_trajectory(contract(), example())["passed"]


@pytest.mark.parametrize(
    "change,expected",
    [
        ("missing_agent", "required_agent"),
        ("tool_failed", "required_tool"),
        ("wrong_args", "arguments"),
        ("wrong_parent", "tool_owner"),
        ("forbidden", "unallowed_tool"),
        ("order", "precedence"),
        ("incomplete", "incomplete_call"),
        ("duplicate", "duplicate_call_id"),
    ],
)
def test_trace_mutations_fail_closed(change, expected):
    from fin_graph.evals.trajectory import grade_trajectory

    events = copy.deepcopy(example())
    if change == "missing_agent":
        events[0]["name"] = events[-1]["name"] = "specialist_cloud"
    elif change == "tool_failed":
        events[2]["status"] = "error"
    elif change == "wrong_args":
        events[1]["args"]["namespace"] = "other"
    elif change == "wrong_parent":
        events[1]["parent"] = "missing"
    elif change == "forbidden":
        events[3]["name"] = events[4]["name"] = "send_email"
    elif change == "order":
        events = [events[0], events[3], events[4], events[1], events[2], events[5]]
    elif change == "incomplete":
        events.pop(2)
    elif change == "duplicate":
        events.insert(2, copy.deepcopy(events[1]))
    for index, event in enumerate(events):
        event["seq"] = index
    grade = grade_trajectory(contract(), events)
    assert not grade["passed"]
    assert any(expected in reason for reason in grade["failures"]), grade


def test_every_case_has_a_contract_with_agents_and_tools():
    from fin_graph.evals.scenarios import load_suite
    from fin_graph.evals.trajectory import contract_for

    for scenario in load_suite("all"):
        value = contract_for(scenario)
        assert value.required_agents
        assert any(rule.min_calls for rule in value.tools)
        assert all(rule.name in value.allowed_tools for rule in value.tools)


def test_missing_trace_and_exhausted_budget_are_failures():
    from fin_graph.evals.trajectory import grade_trajectory

    assert not grade_trajectory(contract(), [])["passed"]
    limited = contract().model_copy(update={"max_tool_calls": 1})
    assert "tool_budget" in grade_trajectory(limited, example())["failures"]


def test_window_expansion_is_checked_against_the_actual_delegation():
    from fin_graph.domain.alerts.parsing import parse_alert
    from fin_graph.domain.window import resolve_window
    from fin_graph.evals.scenarios import load_scenario
    from fin_graph.evals.trajectory import contract_for, grade_trajectory

    scenario = load_scenario("rds_postgres", "001-replication-lag")
    window = resolve_window(
        parse_alert(scenario.alert).started_at, now=scenario.reference_time
    ).expanded()
    definition = contract_for(scenario)
    events = [
        {
            "seq": 0,
            "kind": "agent_start",
            "name": "specialist_cloud",
            "call_id": "a",
            "window": window.model_dump(mode="json"),
        },
        {
            "seq": 1,
            "kind": "tool_start",
            "name": "aws_metric_data",
            "call_id": "t",
            "parent": "a",
            "args": {
                "namespace": "AWS/RDS",
                "metric_name": "CPUUtilization",
                "start": int(window.since.timestamp()),
                "end": int(window.until.timestamp()),
            },
        },
        {"seq": 2, "kind": "tool_end", "name": "aws_metric_data", "call_id": "t", "status": "ok"},
        {"seq": 3, "kind": "agent_end", "name": "specialist_cloud", "call_id": "a", "status": "ok"},
    ]
    result = grade_trajectory(definition, events)
    assert not any("arguments:" in failure for failure in result["failures"]), result
    events[1]["args"]["start"] -= 3600
    result = grade_trajectory(definition, events)
    assert any("arguments:aws_metric_data:window" in failure for failure in result["failures"])
