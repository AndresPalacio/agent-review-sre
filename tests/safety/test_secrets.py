from __future__ import annotations

import json

import pytest

from fin_graph.safety.secrets import redact_secrets, redact_text, redact_value

# Built at runtime so this file holds no string that looks like a real credential.
AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
SLACK_TOKEN = "xoxb-" + "123456789012-abcdefghijkl"
ANTHROPIC_KEY = "sk-ant-" + "api03-abcdefghijklmnopqrstuvwxyz012345"
JWT = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".c2lnbmF0dXJlMTIzNDU2"
PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f"key id {AWS_KEY} in use", "aws_access_key_id"),
        (f"token {GITHUB_TOKEN}", "github_token"),
        (f"slack {SLACK_TOKEN}", "slack_token"),
        (f"anthropic {ANTHROPIC_KEY}", "llm_api_key"),
        (f"session {JWT}", "jwt"),
        (f"key:\n{PRIVATE_KEY}\nend", "private_key"),
    ],
)
def test_known_credential_formats_are_redacted(text: str, kind: str) -> None:
    redacted = redact_secrets(text)

    assert f"[REDACTED:{kind}]" in redacted
    for secret in (AWS_KEY, GITHUB_TOKEN, SLACK_TOKEN, ANTHROPIC_KEY, JWT, "MIIEow"):
        assert secret not in redacted


def test_only_the_secret_part_of_a_bearer_header_is_replaced() -> None:
    redacted = redact_secrets("Authorization: Bearer abcdefghijklmnop0123456789")

    assert redacted == "Authorization: Bearer [REDACTED:bearer_token]"


def test_url_credentials_are_removed_but_the_host_stays() -> None:
    redacted = redact_secrets("dsn=postgres://app:hunter2@db.internal:5432/orders")

    assert "hunter2" not in redacted
    assert "@db.internal:5432/orders" in redacted


@pytest.mark.parametrize(
    "text",
    [
        "DB_PASSWORD=hunter2 next",
        "api_key: hunter2",
        '"client_secret": "hunter2"',
        "x-access-key='hunter2'",
    ],
)
def test_assigned_secrets_lose_their_value_and_keep_their_name(text: str) -> None:
    redacted = redact_secrets(text)

    assert "hunter2" not in redacted
    assert "[REDACTED:assigned_secret]" in redacted


def test_a_value_already_redacted_by_a_specific_rule_keeps_its_kind() -> None:
    redacted = redact_secrets(f'token: "{GITHUB_TOKEN}"')

    assert redacted == 'token: "[REDACTED:github_token]"'


def test_ordinary_operational_text_is_left_alone() -> None:
    text = "pod checkout-7d9f8b6c5-x2x9z restarted 3 times; max_tokens=4096; tokens: 12"

    assert redact_secrets(text) == text


def test_nested_values_are_redacted_and_non_strings_kept() -> None:
    value = {"env": [{"name": "TOKEN", "value": f"token={GITHUB_TOKEN}"}], "restarts": 3}

    redacted = redact_value(value)

    assert redacted == {
        "env": [{"name": "TOKEN", "value": "token=[REDACTED:github_token]"}],
        "restarts": 3,
    }


def test_values_under_secret_named_keys_are_redacted() -> None:
    value = {"DB_PASSWORD": "hunter2", "password": None, "token_count": 5, "replicas": 3}

    assert redact_value(value) == {
        "DB_PASSWORD": "[REDACTED:assigned_secret]",
        "password": None,
        "token_count": 5,
        "replicas": 3,
    }


def test_secrets_in_escaped_json_text_are_redacted() -> None:
    text = 'message="{\\"user\\":\\"app\\",\\"password\\":\\"hunter2\\"}"'

    redacted = redact_secrets(text)

    assert "hunter2" not in redacted
    assert '\\"user\\":\\"app\\"' in redacted


def test_a_quoted_secret_keeps_its_quotes() -> None:
    redacted = redact_secrets('"client_secret": "hunter2"')

    assert redacted == '"client_secret": "[REDACTED:assigned_secret]"'


def test_json_text_is_redacted_structurally_and_stays_valid() -> None:
    text = json.dumps({"password": "hunter2", "log": json.dumps({"api_key": "hunter2-too"})})

    redacted = redact_text(text)

    assert "hunter2" not in redacted
    assert json.loads(redacted)["password"] == "[REDACTED:assigned_secret]"


PEM_BODY = "\n".join("MIIE" + "A" * 60 for _ in range(5))


def test_a_key_whose_header_was_cut_off_is_still_redacted() -> None:
    redacted = redact_secrets(f"{PEM_BODY}\n-----END RSA PRIVATE KEY-----\nserver started")

    assert "MIIE" not in redacted
    assert redacted.endswith("server started")


def test_a_key_whose_footer_was_cut_off_is_redacted_to_the_end() -> None:
    redacted = redact_secrets(f"loading key\n-----BEGIN RSA PRIVATE KEY-----\n{PEM_BODY}")

    assert "MIIE" not in redacted
    assert redacted.startswith("loading key")


STRIPE_KEY = "sk_live_" + "a1b2c3d4e5f6g7h8i9j0k1l2"


@pytest.mark.parametrize(
    ("text", "secret", "kept"),
    [
        ("SECRET_KEY=django-insecure-abc123", "django-insecure-abc123", "SECRET_KEY="),
        ("ENCRYPTION_KEY: 9f8e7d6c5b4a", "9f8e7d6c5b4a", "ENCRYPTION_KEY"),
        ("JWT_SIGNING_KEY=s1gn1ng-k3y", "s1gn1ng-k3y", "JWT_SIGNING_KEY"),
        ("DB_PASS=hunter2", "hunter2", "DB_PASS"),
        ("CREDENTIALS=hunter2", "hunter2", "CREDENTIALS"),
        ("GPG_PASSPHRASE=correct-horse", "correct-horse", "GPG_PASSPHRASE"),
        ("redis://:hunter2@cache:6379/0", "hunter2", "@cache:6379/0"),
        ("postgres://app:hun/ter2@db:5432/orders", "hun/ter2", "@db:5432/orders"),
        ("Authorization: Basic dXNlcjpodW50ZXIy", "dXNlcjpodW50ZXIy", "Authorization: Basic"),
        (f"stripe key {STRIPE_KEY}", STRIPE_KEY, "stripe key"),
        ("DB_PASSWORD=x1y2,z3w4;q5r6 next", "z3w4", "next"),
    ],
)
def test_common_credential_formats_are_redacted(text: str, secret: str, kept: str) -> None:
    redacted = redact_secrets(text)

    assert secret not in redacted
    assert kept in redacted


@pytest.mark.parametrize(
    "text", ["bypass=true", "compass: north", "partition_key=orders", "basic auth is disabled"]
)
def test_words_that_only_look_like_credential_names_are_left_alone(text: str) -> None:
    assert redact_secrets(text) == text


def test_broker_and_market_data_credentials_are_redacted() -> None:
    text = (
        "APCA-API-KEY-ID: PKABCDEF1234567890XY APCA-API-SECRET-KEY=abcDEF123/xyz+987 "
        "plaid access-sandbox-1a2b3c4d-1111-2222-3333-444455556666 "
        "iex pk_0123456789abcdef0123456789abcdef "
        "X-API-KEY: fdk_live_123456 https://api.polygon.io/v2?apiKey=Abc123Def456"
    )

    redacted = redact_secrets(text)

    for secret in (
        "PKABCDEF1234567890XY",
        "abcDEF123/xyz+987",
        "1a2b3c4d-1111",
        "0123456789abcdef0123",
        "fdk_live_123456",
        "Abc123Def456",
    ):
        assert secret not in redacted, secret
    assert redact_secrets("AAPL MSFT PKG AKAM") == "AAPL MSFT PKG AKAM"
