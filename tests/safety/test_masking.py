from __future__ import annotations

import hashlib
import re

import pytest

from sre_graph.config.settings import Settings
from sre_graph.safety.masking import IdentifierKind, Masker, MaskingPolicy

POD = "checkout-7d9f8b6c5-x2x9z"
ENABLED = MaskingPolicy(enabled=True)


def test_a_disabled_policy_changes_nothing() -> None:
    text = f"pod {POD} in namespace: payments"

    assert Masker(MaskingPolicy()).mask(text) == text


def test_identifiers_are_replaced_and_their_labels_kept() -> None:
    masked = Masker(ENABLED).mask(f"pod {POD} in namespace: payments is CrashLoopBackOff")

    assert POD not in masked
    assert "payments" not in masked
    assert re.fullmatch(
        r"pod <POD_[0-9a-f]{10}> in namespace: <NAMESPACE_[0-9a-f]{10}> is CrashLoopBackOff",
        masked,
    )


def test_unmasking_restores_the_original_text() -> None:
    masker = Masker(ENABLED)
    text = "node ip-10-0-1-5.ec2.internal at 10.0.1.5, account 123456789012, oncall@acme.io"

    assert masker.unmask(masker.mask(text)) == text


def test_the_same_value_gets_the_same_token_in_every_call() -> None:
    first = Masker(ENABLED).mask(f"restart {POD}")
    second = Masker(ENABLED).mask(f"logs for {POD}")

    assert first.split()[-1] == second.split()[-1]


def test_tokens_this_masker_did_not_issue_are_left_alone() -> None:
    masker = Masker(ENABLED)
    masker.mask(f"pod {POD}")

    assert masker.unmask("compare <POD_0123456789>") == "compare <POD_0123456789>"


def test_an_email_is_masked_whole_not_as_a_hostname() -> None:
    masked = Masker(ENABLED).mask("page oncall@acme.io now")

    assert re.fullmatch(r"page <EMAIL_[0-9a-f]{10}> now", masked)


def test_only_the_selected_kinds_are_masked() -> None:
    policy = MaskingPolicy(enabled=True, kinds=frozenset({IdentifierKind.IP_ADDRESS}))

    masked = Masker(policy).mask(f"{POD} at 10.0.1.5")

    assert masked.startswith(POD)
    assert "10.0.1.5" not in masked


def test_extra_patterns_mask_with_their_own_label() -> None:
    policy = MaskingPolicy(
        enabled=True, kinds=frozenset(), extra_patterns={"ticket": re.compile(r"TCK-\d+")}
    )

    masked = Masker(policy).mask("see TCK-4411")

    assert re.fullmatch(r"see <TICKET_[0-9a-f]{10}>", masked)


def test_nested_values_are_masked_and_restored() -> None:
    masker = Masker(ENABLED)
    value = {"pods": [POD], "namespace": "namespace: payments", "restarts": 3}

    masked = masker.mask_value(value)

    assert POD not in str(masked)
    assert masked["restarts"] == 3
    assert masker.unmask_value(masked) == value


def test_policy_from_settings_defaults_to_every_kind() -> None:
    policy = MaskingPolicy.from_settings(Settings(_env_file=None, mask_enabled=True))

    assert policy.enabled is True
    assert policy.kinds == frozenset(IdentifierKind)


def test_policy_from_settings_reads_kinds_and_extra_patterns() -> None:
    settings = Settings(
        _env_file=None,
        mask_enabled=True,
        mask_kinds=("pod", "email"),
        mask_extra_regex={"ticket": r"TCK-\d+"},
    )

    policy = MaskingPolicy.from_settings(settings)

    assert policy.kinds == {IdentifierKind.POD, IdentifierKind.EMAIL}
    assert policy.extra_patterns["ticket"].pattern == r"TCK-\d+"


def test_an_unknown_kind_in_settings_is_rejected() -> None:
    with pytest.raises(ValueError, match="podz"):
        MaskingPolicy.from_settings(Settings(_env_file=None, mask_kinds=("podz",)))


def test_json_labels_and_their_values_are_masked() -> None:
    text = '{"namespace":"payments","cluster": "prod-eu-1","app":"checkout"}'

    masked = Masker(ENABLED).mask(text)

    assert "payments" not in masked
    assert "prod-eu-1" not in masked
    assert "checkout" not in masked
    assert masked.startswith('{"namespace":"<NAMESPACE_')


def test_values_under_identifier_keys_are_masked_whole() -> None:
    masker = Masker(ENABLED)
    value = {"namespace": "payments", "labels": {"app": "checkout"}, "replicas": 2}

    masked = masker.mask_value(value)

    assert "payments" not in str(masked)
    assert "checkout" not in str(masked)
    assert masked["replicas"] == 2
    assert masker.unmask_value(masked) == value


def test_tokens_cannot_be_recomputed_by_hashing_a_guess() -> None:
    unkeyed = hashlib.sha256(b"ip_address:10.0.37.201").hexdigest()[:10]

    assert Masker(ENABLED).mask("10.0.37.201") != f"<IP_ADDRESS_{unkeyed}>"


def test_different_keys_give_different_tokens() -> None:
    first = Masker(MaskingPolicy(enabled=True, key=b"a" * 32)).mask("10.0.37.201")
    second = Masker(MaskingPolicy(enabled=True, key=b"b" * 32)).mask("10.0.37.201")

    assert first != second


def test_policy_from_settings_uses_the_configured_key() -> None:
    settings = Settings(_env_file=None, mask_enabled=True, mask_key="k" * 32)

    assert MaskingPolicy.from_settings(settings).key == b"k" * 32
