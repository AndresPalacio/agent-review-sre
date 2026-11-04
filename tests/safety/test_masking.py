from __future__ import annotations

import hashlib
import re

import pytest

from fin_graph.config.settings import Settings
from fin_graph.safety.masking import IdentifierKind, Masker, MaskingPolicy

EMAIL = "analyst@fund.example"
ENABLED = MaskingPolicy(enabled=True)


def test_a_disabled_policy_changes_nothing() -> None:
    text = f"email {EMAIL}"

    assert Masker(MaskingPolicy()).mask(text) == text


def test_identifiers_are_replaced_and_their_labels_kept() -> None:
    masked = Masker(ENABLED).mask("portfolio id PF-2026-001 belongs to analyst@fund.example")

    assert "PF-2026-001" not in masked
    assert EMAIL not in masked
    assert re.fullmatch(
        r"portfolio id <PORTFOLIO_ID_[0-9a-f]{10}> belongs to <EMAIL_[0-9a-f]{10}>",
        masked,
    )


def test_unmasking_restores_the_original_text() -> None:
    masker = Masker(ENABLED)
    text = "client email analyst@fund.example connected from 10.0.1.5"

    assert masker.unmask(masker.mask(text)) == text


def test_the_same_value_gets_the_same_token_in_every_call() -> None:
    first = Masker(ENABLED).mask(f"notify {EMAIL}")
    second = Masker(ENABLED).mask(f"report for {EMAIL}")

    assert first.split()[-1] == second.split()[-1]


def test_tokens_this_masker_did_not_issue_are_left_alone() -> None:
    masker = Masker(ENABLED)
    masker.mask(f"email {EMAIL}")

    assert masker.unmask("compare <EMAIL_0123456789>") == "compare <EMAIL_0123456789>"


def test_an_email_is_masked_whole_not_as_a_hostname() -> None:
    masked = Masker(ENABLED).mask("page oncall@acme.io now")

    assert re.fullmatch(r"page <EMAIL_[0-9a-f]{10}> now", masked)


def test_only_the_selected_kinds_are_masked() -> None:
    policy = MaskingPolicy(enabled=True, kinds=frozenset({IdentifierKind.IP_ADDRESS}))

    masked = Masker(policy).mask(f"{EMAIL} at 10.0.1.5")

    assert masked.startswith(EMAIL)
    assert "10.0.1.5" not in masked


def test_extra_patterns_mask_with_their_own_label() -> None:
    policy = MaskingPolicy(
        enabled=True, kinds=frozenset(), extra_patterns={"ticket": re.compile(r"TCK-\d+")}
    )

    masked = Masker(policy).mask("see TCK-4411")

    assert re.fullmatch(r"see <TICKET_[0-9a-f]{10}>", masked)


def test_nested_values_are_masked_and_restored() -> None:
    masker = Masker(ENABLED)
    value = {"recipients": [EMAIL], "portfolio_id": "PF-2026-001", "positions": 3}

    masked = masker.mask_value(value)

    assert EMAIL not in str(masked)
    assert "PF-2026-001" not in str(masked)
    assert masked["positions"] == 3
    assert masker.unmask_value(masked) == value


def test_policy_from_settings_defaults_to_every_kind() -> None:
    policy = MaskingPolicy.from_settings(Settings(_env_file=None, mask_enabled=True))

    assert policy.enabled is True
    assert policy.kinds == frozenset(IdentifierKind)


def test_policy_from_settings_reads_kinds_and_extra_patterns() -> None:
    settings = Settings(
        _env_file=None,
        mask_enabled=True,
        mask_kinds=("portfolio_id", "email"),
        mask_extra_regex={"ticket": r"TCK-\d+"},
    )

    policy = MaskingPolicy.from_settings(settings)

    assert policy.kinds == {IdentifierKind.PORTFOLIO_ID, IdentifierKind.EMAIL}
    assert policy.extra_patterns["ticket"].pattern == r"TCK-\d+"


def test_an_unknown_kind_in_settings_is_rejected() -> None:
    with pytest.raises(ValueError, match="accountz"):
        MaskingPolicy.from_settings(Settings(_env_file=None, mask_kinds=("accountz",)))


def test_financial_labels_and_their_values_are_masked() -> None:
    text = 'portfolio_id="PF-2026-001", email="analyst@fund.example"'

    masked = Masker(ENABLED).mask(text)

    assert "PF-2026-001" not in masked
    assert EMAIL not in masked
    assert masked.startswith('portfolio_id="<PORTFOLIO_ID_')


def test_values_under_identifier_keys_are_masked_whole() -> None:
    masker = Masker(ENABLED)
    value = {
        "portfolio_id": "PF-2026-001",
        "owner": {"client_name": "Ana GÃ³mez"},
        "positions": 2,
    }

    masked = masker.mask_value(value)

    assert "PF-2026-001" not in str(masked)
    assert "Ana GÃ³mez" not in str(masked)
    assert masked["positions"] == 2
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


def test_financial_identifiers_are_masked_and_market_data_is_not() -> None:
    masker = Masker(MaskingPolicy(enabled=True))
    text = (
        "Cliente: Juan Pérez, cuenta número 0123-4567-8901, tarjeta 4111 1111 1111 1111, "
        "IBAN DE89 3704 0044 0532 0130 00, routing 021000021, ssn 123-45-6789, "
        "phone +57 300 123 4567, portfolio id PF-2026-001"
    )
    market = (
        "AAPL closed at 179.66, volume 73450582, market cap 2994371342560; NVDA EV 4200000000000"
    )

    masked = masker.mask(text)

    for secret in (
        "Juan Pérez",
        "0123-4567-8901",
        "4111 1111 1111 1111",
        "DE89",
        "021000021",
        "123-45-6789",
        "+57 300",
        "PF-2026-001",
    ):
        assert secret not in masked, secret
    assert masker.unmask(masked) == text
    assert masker.mask(market) == market


def test_checksums_keep_lookalike_numbers_unmasked() -> None:
    masker = Masker(MaskingPolicy(enabled=True))

    assert masker.mask("card 4111 1111 1111 1112") == "card 4111 1111 1111 1112"
    assert masker.mask("IBAN DE00 3704 0044 0532 0130 00") == "IBAN DE00 3704 0044 0532 0130 00"
    assert masker.mask("routing 123456789") == "routing 123456789"


def test_financial_keys_mask_structured_values_but_never_tickers() -> None:
    masker = Masker(MaskingPolicy(enabled=True))

    masked = masker.mask_value(
        {"account_number": "998877", "ticker": "TSLA", "client_name": "Ana Gómez"}
    )

    assert masked["ticker"] == "TSLA"
    assert masked["account_number"].startswith("<ACCOUNT_NUMBER_")
    assert masked["client_name"].startswith("<CLIENT_NAME_")


def test_twelve_digit_financial_figures_are_not_identifiers() -> None:
    masker = Masker(MaskingPolicy(enabled=True))
    figures = '{"revenue": 383000000000.0, "market_cap": 345310000000}'

    assert masker.mask(figures) == figures


def test_identifier_kinds_are_exclusively_financial() -> None:
    assert {item.value for item in IdentifierKind} == {
        "ip_address",
        "email",
        "account_number",
        "iban",
        "card_number",
        "routing_number",
        "tax_id",
        "phone",
        "portfolio_id",
        "client_name",
    }
