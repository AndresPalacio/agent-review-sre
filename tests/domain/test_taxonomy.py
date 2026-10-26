from __future__ import annotations

import pytest

from fin_graph.domain.taxonomy import (
    CATEGORIES,
    CATEGORY_NAMES,
    HEALTHY,
    UNKNOWN,
    CategoryGroup,
    category,
    category_text_mismatch,
    normalize_category,
    render_taxonomy,
)


def test_the_taxonomy_has_the_ninety_six_general_categories_with_unique_names() -> None:
    assert len(CATEGORIES) == 96
    assert len(CATEGORY_NAMES) == 96
    assert {HEALTHY, UNKNOWN, "pod_oomkilled", "resource_exhaustion"} <= CATEGORY_NAMES


def test_categories_are_looked_up_by_name() -> None:
    oom = category("pod_oomkilled")

    assert oom is not None
    assert oom.group is CategoryGroup.KUBERNETES
    assert category("not_a_category") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pod_oomkilled", "pod_oomkilled"),
        (" Pod OOMKilled ", "pod_oomkilled"),
        ("dns-resolution-failure", "dns_resolution_failure"),
        ("oom_killed", "pod_oomkilled"),
        ("config_error", "configuration_error"),
        ("made_up_category", UNKNOWN),
        ("", UNKNOWN),
    ],
)
def test_model_labels_are_normalized_and_fail_closed(raw: str, expected: str) -> None:
    assert normalize_category(raw) == expected


def test_a_category_outside_the_allowed_set_becomes_unknown() -> None:
    assert normalize_category("pod_oomkilled", allowed={"connection_exhaustion"}) == UNKNOWN


def test_the_prompt_taxonomy_lists_groups_in_order_with_descriptions() -> None:
    rendered = render_taxonomy({"pod_oomkilled", "connection_exhaustion", "unknown"})

    lines = rendered.splitlines()
    assert lines[0] == "### database"
    assert lines[1].startswith("- connection_exhaustion: ")
    assert "### kubernetes_workload" in lines
    assert lines[-1].startswith("- unknown: ")


def test_root_cause_text_from_another_group_is_flagged() -> None:
    text = "Postgres max_connections was exhausted by a connection pool leak."

    reason = category_text_mismatch(text, "pod_oomkilled")

    assert reason is not None
    assert "database" in reason


@pytest.mark.parametrize(
    ("text", "name"),
    [
        (
            "Postgres max_connections was exhausted by a connection pool leak.",
            "connection_pool_leak",
        ),
        ("The pod was OOMKilled after a memory leak.", "pod_oomkilled"),
        ("A bad deploy changed the postgres connection pool size.", "bad_deploy"),
        ("Everything is fine.", HEALTHY),
        ("Only one postgres mention.", "pod_oomkilled"),
    ],
)
def test_text_that_agrees_or_is_inconclusive_is_not_flagged(text: str, name: str) -> None:
    assert category_text_mismatch(text, name) is None
