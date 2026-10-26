from __future__ import annotations

import copy

import pytest


def example():
    return [
        {"seq": 0, "kind": "agent_start", "name": "planner", "call_id": "p"},
        {"seq": 1, "kind": "agent_end", "name": "planner", "call_id": "p", "status": "ok"},
        {"seq": 2, "kind": "agent_start", "name": "analyst_graham", "call_id": "a"},
        {
            "seq": 3,
            "kind": "tool_start",
            "name": "compute_graham",
            "call_id": "t",
            "parent": "a",
            "args": {"ticker": "DEEP", "as_of": "2024-06-28"},
        },
        {
            "seq": 4,
            "kind": "tool_end",
            "name": "compute_graham",
            "call_id": "t",
            "status": "ok",
        },
        {
            "seq": 5,
            "kind": "agent_end",
            "name": "analyst_graham",
            "call_id": "a",
            "status": "ok",
        },
        {"seq": 6, "kind": "agent_start", "name": "synthesizer", "call_id": "s"},
        {
            "seq": 7,
            "kind": "agent_end",
            "name": "synthesizer",
            "call_id": "s",
            "status": "ok",
        },
    ]


def contract():
    from fin_graph.evals.market.trajectory import AnalysisContract

    return AnalysisContract.model_validate(
        {
            "required_agents": ["planner", "analyst_graham", "synthesizer"],
            "allowed_tools": ["compute_graham"],
            "tools": [
                {
                    "name": "compute_graham",
                    "agent": "analyst_graham",
                    "min_calls": 1,
                    "max_calls": 1,
                    "tickers": ["DEEP"],
                    "as_of": "2024-06-28",
                }
            ],
            "precedence": [
                ["agent:planner", "agent:analyst_graham"],
                ["agent:analyst_graham", "agent:synthesizer"],
            ],
            "max_tool_calls": 1,
        }
    )


def test_observed_trace_satisfies_agent_tool_arguments_and_dependency():
    from fin_graph.evals.market.trajectory import grade_trajectory

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
    from fin_graph.evals.market.trajectory import grade_trajectory

    events = copy.deepcopy(example())
    if change == "missing_agent":
        events[2]["name"] = events[5]["name"] = "analyst_buffett"
    elif change == "tool_failed":
        events[4]["status"] = "error"
    elif change == "wrong_args":
        events[3]["args"]["ticker"] = "OTHER"
    elif change == "wrong_parent":
        events[3]["parent"] = "missing"
    elif change == "forbidden":
        events[3]["name"] = events[4]["name"] = "place_order"
    elif change == "order":
        events = [*events[:2], *events[6:8], *events[2:6]]
    elif change == "incomplete":
        events.pop(4)
    elif change == "duplicate":
        events.insert(4, copy.deepcopy(events[3]))
    for index, event in enumerate(events):
        event["seq"] = index
    grade = grade_trajectory(contract(), events)
    assert not grade["passed"]
    assert any(expected in reason for reason in grade["failures"]), grade


def test_every_case_has_a_contract_with_agents_and_tools():
    from fin_graph.evals.market.scenarios import load_equity_suite
    from fin_graph.evals.market.trajectory import contract_for

    for scenario in load_equity_suite("all"):
        value = contract_for(scenario)
        assert value.required_agents
        assert any(rule.min_calls for rule in value.tools)
        assert all(rule.name in value.allowed_tools for rule in value.tools)


def test_missing_trace_and_exhausted_budget_are_failures():
    from fin_graph.evals.market.trajectory import grade_trajectory

    assert not grade_trajectory(contract(), [])["passed"]
    limited = contract().model_copy(update={"max_agent_calls": 2})
    assert "agent_budget" in grade_trajectory(limited, example())["failures"]


def test_ticker_and_analysis_date_are_checked_against_the_actual_delegation():
    from fin_graph.evals.market.trajectory import grade_trajectory

    events = example()
    result = grade_trajectory(contract(), events)
    assert not any("arguments:" in failure for failure in result["failures"]), result
    events[3]["args"]["as_of"] = "2024-06-29"
    result = grade_trajectory(contract(), events)
    assert "arguments:compute_graham:as_of" in result["failures"]
