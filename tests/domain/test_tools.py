from __future__ import annotations

from typing import Any

import pytest
from langchain_core.tools import tool

from fin_graph.domain.tools import (
    Domain,
    EvidenceType,
    SideEffectLevel,
    ToolMeta,
    attach_tool_meta,
    read_tool_meta,
)


@tool
def list_pods(namespace: str) -> str:
    """List pods."""
    return namespace


def _meta(**overrides: Any) -> ToolMeta:
    values: dict[str, Any] = {
        "integration": "kubernetes",
        "domains": frozenset({Domain.FUNDAMENTALS, Domain.TECHNICALS}),
        "evidence_type": EvidenceType.TOPOLOGY,
    }
    values.update(overrides)
    return ToolMeta(**values)


def test_metadata_is_flat_and_json_friendly() -> None:
    assert _meta().as_metadata() == {
        "integration": "kubernetes",
        "domains": ["fundamentals", "technicals"],
        "evidence_type": "topology",
        "side_effect_level": "read_only",
        "idempotent": True,
    }


def test_metadata_round_trips_through_a_tool() -> None:
    meta = _meta(side_effect_level=SideEffectLevel.MUTATING, idempotent=False)
    tagged = attach_tool_meta(list_pods.model_copy(), meta)

    assert read_tool_meta(tagged) == meta


def test_attaching_keeps_unrelated_metadata() -> None:
    plain = list_pods.model_copy(update={"metadata": {"owner": "sre"}})

    tagged = attach_tool_meta(plain, _meta())

    assert tagged.metadata is not None
    assert tagged.metadata["owner"] == "sre"


def test_a_tool_without_metadata_declares_nothing() -> None:
    assert read_tool_meta(list_pods.model_copy()) is None
    assert read_tool_meta(None) is None


def test_malformed_metadata_is_rejected() -> None:
    broken = {**_meta().as_metadata(), "side_effect_level": "sometimes"}

    with pytest.raises(ValueError, match="sometimes"):
        ToolMeta.from_metadata(broken)
