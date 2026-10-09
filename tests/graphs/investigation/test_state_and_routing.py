from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from sre_graph.domain.tools import (
    Domain,
    EvidenceType,
    SideEffectLevel,
    ToolMeta,
    attach_tool_meta,
)
from sre_graph.graphs.investigation.specialists import SPECIALISTS, read_tools
from sre_graph.graphs.investigation.state import merge_findings


@tool
def lookup(query: str) -> str:
    """Read matching items."""
    return query


def candidate(name: str, domain: Domain, effect: SideEffectLevel) -> Any:
    copy = lookup.model_copy(deep=True)
    copy.name = name
    return attach_tool_meta(
        copy,
        ToolMeta(
            integration="fixture",
            domains=frozenset({domain}),
            evidence_type=EvidenceType.EVENTS,
            side_effect_level=effect,
        ),
    )


def test_all_seven_specialists_exist_and_only_read_tools_route() -> None:
    assert set(SPECIALISTS) == set(Domain)
    tools = [
        candidate("metric", Domain.METRICS, SideEffectLevel.READ_ONLY),
        candidate("delete", Domain.METRICS, SideEffectLevel.MUTATING),
        candidate("notify", Domain.METRICS, SideEffectLevel.EXTERNAL),
        candidate("logs", Domain.LOGS, SideEffectLevel.READ_ONLY),
    ]
    assert [item.name for item in read_tools(tools, Domain.METRICS)] == ["metric"]


def test_findings_reducer_replaces_a_retried_branch_and_stays_json_serializable() -> None:
    first = [{"wave": 1, "domain": "metrics", "summary": "old", "confidence": 0.2}]
    retry = [
        {"wave": 1, "domain": "metrics", "summary": "new", "confidence": 0.8},
        {"wave": 1, "domain": "logs", "summary": "log", "confidence": 0.5},
    ]
    merged = merge_findings(first, retry)
    assert [(item["domain"], item["summary"]) for item in merged] == [
        ("metrics", "new"),
        ("logs", "log"),
    ]
    json.dumps(merged)
