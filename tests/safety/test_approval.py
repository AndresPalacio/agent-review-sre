from __future__ import annotations

import pytest

from fin_graph.config.settings import Settings
from fin_graph.domain.tools import Domain, EvidenceType, SideEffectLevel, ToolMeta
from fin_graph.safety.approval import ApprovalPolicy


def _meta(level: SideEffectLevel) -> ToolMeta:
    return ToolMeta(
        integration="kubernetes",
        domains=frozenset({Domain.PORTFOLIO}),
        evidence_type=EvidenceType.TOPOLOGY,
        side_effect_level=level,
    )


@pytest.mark.parametrize("environment", ["dev", "prod"])
def test_read_only_calls_never_wait(environment: str) -> None:
    policy = ApprovalPolicy(environment=environment)

    assert policy.requires_approval("k8s_list_pods", _meta(SideEffectLevel.READ_ONLY)) is False


@pytest.mark.parametrize("level", [SideEffectLevel.MUTATING, SideEffectLevel.EXTERNAL])
def test_state_changing_calls_wait_by_default(level: SideEffectLevel) -> None:
    assert ApprovalPolicy().requires_approval("k8s_rollout_undo", _meta(level)) is True


def test_auto_approved_tools_skip_the_wait_outside_production() -> None:
    policy = ApprovalPolicy(environment="staging", auto_approve=frozenset({"k8s_rollout_undo"}))

    assert policy.requires_approval("k8s_rollout_undo", _meta(SideEffectLevel.MUTATING)) is False
    assert policy.requires_approval("k8s_scale", _meta(SideEffectLevel.MUTATING)) is True


def test_production_ignores_the_auto_approve_list() -> None:
    policy = ApprovalPolicy(environment="prod", auto_approve=frozenset({"k8s_rollout_undo"}))

    assert policy.requires_approval("k8s_rollout_undo", _meta(SideEffectLevel.MUTATING)) is True


def test_a_tool_without_metadata_is_treated_as_changing_state() -> None:
    assert ApprovalPolicy().requires_approval("mystery", None) is True


def test_policy_from_settings() -> None:
    settings = Settings(_env_file=None, environment="Prod", auto_approve_tools=("a", "b"))

    policy = ApprovalPolicy.from_settings(settings)

    assert policy == ApprovalPolicy(environment="prod", auto_approve=frozenset({"a", "b"}))


def test_named_approver_allowlist_restricts_mutation_decisions() -> None:
    policy = ApprovalPolicy(environment="prod", allowed_approvers=frozenset({"key-hash-a"}))

    assert policy.allows_approver("key-hash-a", "approver")
    assert not policy.allows_approver("key-hash-b", "approver")
    assert not policy.allows_approver("key-hash-a", "viewer")


@pytest.mark.parametrize("environment", ["production", "prd", "prod-eu", "qa"])
def test_any_environment_not_known_as_non_production_ignores_auto_approve(
    environment: str,
) -> None:
    policy = ApprovalPolicy(environment=environment, auto_approve=frozenset({"k8s_rollout_undo"}))

    assert policy.requires_approval("k8s_rollout_undo", _meta(SideEffectLevel.MUTATING)) is True


@pytest.mark.parametrize("environment", ["dev", "development", "local", "test", "staging"])
def test_auto_approve_applies_in_known_non_production_environments(environment: str) -> None:
    policy = ApprovalPolicy(environment=environment, auto_approve=frozenset({"k8s_rollout_undo"}))

    assert policy.requires_approval("k8s_rollout_undo", _meta(SideEffectLevel.MUTATING)) is False
