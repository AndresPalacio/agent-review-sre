from __future__ import annotations

import pytest

from fin_graph.compat.settings import CompatibilityProfile, resolve_alias
from fin_graph.config.settings import Settings


def test_resolve_alias_accepts_legacy_only_and_equal_dual_values() -> None:
    assert (
        resolve_alias(
            {"DEFAULT_MODEL_PROVIDER": "Ollama"},
            "LLM_PROVIDER",
            ("DEFAULT_MODEL_PROVIDER",),
        )
        == "Ollama"
    )
    assert (
        resolve_alias(
            {"LLM_PROVIDER": "ollama", "DEFAULT_MODEL_PROVIDER": "ollama"},
            "LLM_PROVIDER",
            ("DEFAULT_MODEL_PROVIDER",),
        )
        == "ollama"
    )


def test_resolve_alias_reports_names_without_leaking_conflicting_values() -> None:
    with pytest.raises(ValueError) as caught:
        resolve_alias(
            {"LLM_PROVIDER": "modern-secret", "DEFAULT_MODEL_PROVIDER": "legacy-secret"},
            "LLM_PROVIDER",
            ("DEFAULT_MODEL_PROVIDER",),
        )

    message = str(caught.value)
    assert "LLM_PROVIDER" in message
    assert "DEFAULT_MODEL_PROVIDER" in message
    assert "modern-secret" not in message
    assert "legacy-secret" not in message


def test_settings_load_reference_environment_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "Ollama")
    monkeypatch.setenv("DEFAULT_MODEL_NAME", "llama3.2:3b")
    monkeypatch.setenv("DATA_PROVIDER", "yahoo")

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "ollama"
    assert settings.llm_model_reasoning == "llama3.2:3b"
    assert settings.llm_model_worker == "llama3.2:3b"
    assert settings.llm_model_fast == "llama3.2:3b"
    assert settings.llm_model_judge == "llama3.2:3b"
    assert settings.market_data_provider == "yahoo"


def test_settings_reject_conflicting_modern_and_legacy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "ollama")

    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None)

    message = str(caught.value)
    assert "LLM_PROVIDER" in message
    assert "DEFAULT_MODEL_PROVIDER" in message
    assert "anthropic" not in message
    assert "ollama" not in message


def test_untrusted_forwarded_header_cannot_turn_remote_peer_into_loopback() -> None:
    profile = CompatibilityProfile(trusted_proxies=("10.0.0.0/8",))

    assert profile.resolve_client_host("203.0.113.9", "127.0.0.1") == "203.0.113.9"
    assert profile.resolve_client_host("10.2.3.4", "198.51.100.7, 10.2.3.4") == "198.51.100.7"


def test_local_profile_binds_loopback_and_rejects_insecure_remote_legacy_api() -> None:
    profile = CompatibilityProfile()

    assert profile.default_bind_host == "127.0.0.1"
    profile.validate_bind("127.0.0.1")
    with pytest.raises(ValueError, match="ALLOW_INSECURE_LEGACY_API"):
        profile.validate_bind("0.0.0.0")


def test_explicit_insecure_opt_in_allows_remote_legacy_bind() -> None:
    profile = CompatibilityProfile(local=False, allow_insecure_remote=True)

    assert profile.default_bind_host == "0.0.0.0"
    profile.validate_bind("0.0.0.0")
