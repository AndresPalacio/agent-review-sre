from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from fin_graph.agents.catalog import (
    agent_by_id,
    agent_catalog,
    resolve_factory,
    validate_agent_catalog,
)


def test_catalog_has_unique_resolvable_agents_and_nineteen_personas() -> None:
    catalog = agent_catalog()

    assert len({item.id for item in catalog}) == len(catalog)
    assert len([item for item in catalog if item.id.startswith("analysis.persona.")]) == 19
    assert all(callable(resolve_factory(item)) for item in catalog)
    ids = {item.id for item in catalog}
    assert all(set(item.children) <= ids for item in catalog)


def test_catalog_includes_optional_optimizer_but_no_workflow_nodes() -> None:
    ids = {item.id for item in agent_catalog()}

    assert "optimizer.proposer" in ids
    assert not ids & {
        "analysis.verify",
        "analysis.risk",
        "orders.route",
        "market_watch.poll",
    }


def test_invalid_catalog_fails_closed() -> None:
    baseline = agent_catalog()

    with pytest.raises(ValueError, match="duplicate"):
        validate_agent_catalog((*baseline, baseline[0]))
    with pytest.raises(ValueError, match="child"):
        validate_agent_catalog((replace(baseline[0], children=("missing",)), *baseline[1:]))
    with pytest.raises(ImportError):
        validate_agent_catalog(
            (replace(baseline[0], factory_path="missing.module:factory"), *baseline[1:])
        )


def test_catalog_rejects_legacy_langchain_agent_kind() -> None:
    baseline = agent_catalog()

    with pytest.raises(ValueError, match="unknown agent kind"):
        validate_agent_catalog(
            (replace(baseline[0], kind=cast(Any, "langchain_agent")), *baseline[1:])
        )


def test_native_react_is_a_deepagents_subagent() -> None:
    assert agent_by_id("analysis.native_react").kind == "deepagents_subagent"


def test_descriptor_export_and_lookup_are_stable() -> None:
    descriptor = agent_by_id("analysis.persona_team")

    assert descriptor.to_dict()["id"] == "analysis.persona_team"
    assert descriptor.to_dict()["children"] == list(descriptor.children)
    with pytest.raises(KeyError, match="missing"):
        agent_by_id("missing")
